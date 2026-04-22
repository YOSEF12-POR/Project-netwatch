import ipaddress
import json
import re
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from functools import wraps

from flask import jsonify, request, session

from app.models.models import AuditLog, db


ID_RE = re.compile(r"^\d+$")
NAME_RE = re.compile(r"^[a-zA-Z0-9\-_.]+$")
IP_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}(\/\d{1,2})?$")
MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")

FORBIDDEN_WORDS = (
    "/system reset",
    "/system reboot",
    "/system shutdown",
    "/tool fetch",
    "/file",
    "export",
    "import",
    "/certificate",
    "/user set",
    "password",
)
FORBIDDEN_CHUNKS = (";", "&&", "||", "|", "`", "$", "(", ")", "<", ">", "\n", "\r", "..")

_RATE_LIMITS = {
    "public": (10, 15 * 60),
    "read": (60, 60),
    "write": (10, 60),
}
_rate_lock = threading.Lock()
_rate_buckets = defaultdict(deque)


def _json_error(message: str, status: int, field: str = None):
    payload = {"success": False, "error": message}
    if field:
        payload["field"] = field
    return jsonify(payload), status


def rate_limit(scope: str):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            limited, resp = enforce_rate_limit(scope)
            if limited:
                return resp
            return func(*args, **kwargs)

        return wrapper

    return decorator


def enforce_rate_limit(scope: str):
    limit, window = _RATE_LIMITS[scope]
    if scope == "public":
        key = f"public:{request.remote_addr or 'unknown'}"
    else:
        user_id = session.get("user_id")
        key = f"{scope}:{user_id}:{request.endpoint}"
    now = time.time()
    with _rate_lock:
        bucket = _rate_buckets[key]
        while bucket and (now - bucket[0]) > window:
            bucket.popleft()
        if len(bucket) >= limit:
            return True, _json_error("Too many requests", 429)
        bucket.append(now)
    return False, None


def validate_id(value) -> bool:
    return bool(ID_RE.fullmatch(str(value or "")))


def validate_name(value: str) -> bool:
    return bool(NAME_RE.fullmatch((value or "").strip()))


def validate_ip(value: str, allow_cidr: bool = True) -> bool:
    raw = (value or "").strip()
    if not IP_RE.fullmatch(raw):
        return False
    try:
        if "/" in raw and allow_cidr:
            ipaddress.ip_network(raw, strict=False)
        elif "/" not in raw:
            ipaddress.ip_address(raw)
        else:
            return False
    except Exception:
        return False
    return True


def validate_mac(value: str) -> bool:
    return bool(MAC_RE.fullmatch((value or "").strip()))


def contains_forbidden_text(value: str) -> bool:
    lowered = (value or "").lower()
    if any(chunk in lowered for chunk in FORBIDDEN_CHUNKS):
        return True
    if any(word in lowered for word in FORBIDDEN_WORDS):
        return True
    return False


def require_role(*allowed_roles):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            user = getattr(request, "current_user", None)
            if not user:
                return _json_error("Unauthorized", 401)
            if allowed_roles and user.role not in allowed_roles:
                return _json_error("Forbidden", 403)
            return func(*args, **kwargs)

        return wrapper

    return decorator


def write_audit_log(user_id, router_id, action, params, result, status_code):
    try:
        details = {
            "routerId": router_id,
            "params": params or {},
            "result": result,
            "statusCode": status_code,
            "timestamp": int(time.time()),
        }
        log = AuditLog(
            user_id=user_id,
            action=action,
            ip_address=request.remote_addr,
            details=json.dumps(details, ensure_ascii=False),
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()


class RouterCommandQueue:
    """Queue لكل راوتر: تنفيذ متسلسل + انتظار محدود."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = {}

    def _get_state(self, router_id):
        st = self._state.get(router_id)
        if st is None:
            st = {"running": 0, "pending": 0, "cond": threading.Condition(self._lock)}
            self._state[router_id] = st
        return st

    def run(self, router_id, callable_, timeout_seconds=10, max_pending=5):
        with self._lock:
            st = self._get_state(router_id)
            if st["pending"] >= max_pending:
                return False, "QUEUE_FULL", None
            st["pending"] += 1
            start = time.time()
            while st["running"] >= 1:
                remaining = timeout_seconds - (time.time() - start)
                if remaining <= 0:
                    st["pending"] -= 1
                    return False, "QUEUE_TIMEOUT", None
                st["cond"].wait(timeout=remaining)
            st["pending"] -= 1
            st["running"] += 1

        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(callable_)
                try:
                    value = fut.result(timeout=timeout_seconds)
                except FutureTimeoutError:
                    return False, "COMMAND_TIMEOUT", None
            return True, "OK", value
        finally:
            with self._lock:
                st = self._get_state(router_id)
                st["running"] = max(0, st["running"] - 1)
                st["cond"].notify_all()
