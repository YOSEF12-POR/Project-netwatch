"""
🔌 API Controllers - API endpoints للشبكات والأجهزة
"""

from flask import Blueprint, request, jsonify, session
from app.models.models import Device, Network, User, db
from app.services.network_service import NetworkService
from app.services.device_service import DeviceService
from app.services.mikrotik_service import MikroTikService
from app.utils.connection_manager import ConnectionManager
from app.utils.security import (
    contains_forbidden_text,
    enforce_rate_limit,
    require_role,
    validate_ip,
    validate_mac,
    validate_name,
    write_audit_log,
)
from datetime import datetime
import logging
from functools import wraps

api_bp = Blueprint('api', __name__, url_prefix='/api')
logger = logging.getLogger(__name__)


# ==================== Helper Functions ====================

def get_user_id():
    """الحصول على معرف المستخدم من الجلسة"""
    return session.get('user_id')


def require_login(f):
    """ديكوريتور للتحقق من تسجيل الدخول"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        user_id = get_user_id()
        if not user_id:
            return jsonify({"success": False, "error": "Unauthorized"}), 401
        request.current_user = User.query.get(user_id)
        if not request.current_user:
            return jsonify({"success": False, "error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


# ==================== Session Helpers ====================
@api_bp.before_request
def api_rate_limit_guard():
    # health endpoint عام
    if request.endpoint == "api.health_check":
        return None
    scope = "read" if request.method == "GET" else "write"
    limited, resp = enforce_rate_limit(scope)
    if limited:
        return resp
    return None

@api_bp.route('/mikrotik/authenticated', methods=['POST'])
@require_login
def mark_mikrotik_authenticated():
    """
    تعليم الجلسة بأن المستخدم أدخل بيانات MikroTik لهذا الدخول.
    (لا يعني بالضرورة نجاح الاتصال الفعلي، فقط أنه تم إدخال البيانات/حفظها)
    """
    return jsonify({"success": True})


@api_bp.route('/mikrotik/network-info', methods=['GET'])
@require_login
def mikrotik_network_info():
    """عنوان الراوتر والمنفذ من قاعدة البيانات + هل جلسة MikroTik مفعّلة."""
    try:
        user_id = get_user_id()
        user = User.query.get(user_id)
        if not user:
            return jsonify({"success": False, "message": "❌ المستخدم غير موجود"}), 404

        networks = user.get_networks()
        network = networks[0] if networks else None
        if not network:
            return jsonify({
                "success": True,
                "has_network": False,
                "session_active": False,
            })

        bag = session.get("mikrotik_creds") or {}
        entry = bag.get(str(network.id)) or {}
        session_active = bool(
            (entry.get("username") or "").strip() and (entry.get("password") or "")
        )
        return jsonify({
            "success": True,
            "has_network": True,
            "network_id": network.id,
            "mikrotik_ip": network.mikrotik_ip or "",
            "mikrotik_port": int(network.mikrotik_port or 8728),
            "session_active": session_active,
        })
    except Exception as e:
        logger.error(f"❌ خطأ في mikrotik_network_info: {e}")
        return jsonify({"success": False, "message": f"❌ خطأ: {str(e)}"}), 500


# ==================== Networks API ====================

@api_bp.route('/networks', methods=['GET'])
@require_login
def list_networks():
    """الحصول على جميع شبكات المستخدم"""
    try:
        user_id = get_user_id()
        networks = NetworkService.get_user_networks(user_id)
        return jsonify({"success": True, "networks": networks})
    except Exception as e:
        logger.error(f"❌ خطأ في جلب الشبكات: {e}")
        return jsonify({"success": False, "message": f"❌ خطأ: {str(e)}"}), 500


@api_bp.route('/networks/<int:network_id>', methods=['GET'])
@require_login
def get_network(network_id):
    """الحصول على تفاصيل شبكة معينة"""
    try:
        user_id = get_user_id()
        network = NetworkService.get_network_details(network_id, user_id)

        if not network:
            return jsonify({"success": False, "message": "❌ الشبكة غير موجودة"}), 404

        return jsonify({"success": True, "network": network})
    except Exception as e:
        return jsonify({"success": False, "message": f"❌ خطأ: {str(e)}"}), 500


@api_bp.route('/networks', methods=['POST'])
@require_login
def create_network():
    """إنشاء شبكة جديدة"""
    try:
        user_id = get_user_id()
        data = request.get_json()

        if not data:
            return jsonify({"success": False, "message": "❌ لا توجد بيانات"}), 400

        success, message, network_id = NetworkService.create_network(
            user_id=user_id,
            name=data.get('name'),
            ip=data.get('ip'),
            port=int(data.get('port', 22)),
            mikrotik_user=data.get('mikrotik_user', 'admin'),
            mikrotik_pass=data.get('mikrotik_pass'),
        )

        if not success:
            return jsonify({"success": False, "message": message}), 400

        return jsonify({"success": True, "message": message, "network_id": network_id}), 201

    except Exception as e:
        logger.error(f"❌ خطأ في إنشاء الشبكة: {e}")
        return jsonify({"success": False, "message": f"❌ خطأ: {str(e)}"}), 500


@api_bp.route('/networks/<int:network_id>/test', methods=['POST'])
@require_login
def test_network_connection(network_id):
    """اختبار الاتصال بشبكة"""
    try:
        user_id = get_user_id()
        success, message = NetworkService.test_connection(network_id, user_id)

        status_code = 200 if success else 400

        return jsonify({"success": success, "message": message}), status_code

    except Exception as e:
        return jsonify({"success": False, "message": f"❌ خطأ: {str(e)}"}), 500


@api_bp.route('/networks/<int:network_id>', methods=['DELETE'])
@require_login
def delete_network(network_id):
    """حذف شبكة"""
    try:
        user_id = get_user_id()
        success, message = NetworkService.delete_network(network_id, user_id)

        if not success:
            return jsonify({"success": False, "message": message}), 400

        return jsonify({"success": True, "message": message})

    except Exception as e:
        return jsonify({"success": False, "message": f"❌ خطأ: {str(e)}"}), 500


@api_bp.route('/networks/<int:network_id>/interfaces', methods=['GET'])
@require_login
def get_interfaces(network_id):
    """الحصول على واجهات الشبكة"""
    try:
        user_id = get_user_id()
        interfaces = NetworkService.get_network_interfaces(network_id, user_id)

        return jsonify({"success": True, "interfaces": interfaces})

    except Exception as e:
        return jsonify({"success": False, "message": f"❌ خطأ: {str(e)}"}), 500


@api_bp.route('/networks/<int:network_id>/health', methods=['GET'])
@require_login
def get_network_health(network_id):
    """الحصول على صحة الشبكة"""
    try:
        user_id = get_user_id()
        health = NetworkService.get_network_health(network_id, user_id)

        return jsonify({"success": True, "health": health})

    except Exception as e:
        return jsonify({"success": False, "message": f"❌ خطأ: {str(e)}"}), 500


# ==================== Devices API ====================

@api_bp.route('/networks/<int:network_id>/devices', methods=['GET'])
@require_login
def list_devices(network_id):
    """الحصول على أجهزة الشبكة"""
    try:
        user_id = get_user_id()
        # التحقق من ملكية الشبكة
        network = NetworkService.get_network_details(network_id, user_id)
        if not network:
            return jsonify({"success": False, "message": "❌ الشبكة غير موجودة"}), 404

        devices = DeviceService.get_network_devices(network_id)

        return jsonify({"success": True, "devices": devices})

    except Exception as e:
        return jsonify({"success": False, "message": f"❌ خطأ: {str(e)}"}), 500


@api_bp.route('/networks/<int:network_id>/scan', methods=['POST'])
@require_login
@require_role("admin", "distributor")
def scan_network(network_id):
    """
    مسح الشبكة — يعيد قائمة الأجهزة فقط (بدون حفظ في الجدول).
    استخدم POST .../scan/save لحفظ النتائج مع UP/DOWN من ping الراوتر.
    """
    try:
        user_id = get_user_id()
        network = NetworkService.get_network_details(network_id, user_id)
        if not network:
            return jsonify({"success": False, "message": "❌ الشبكة غير موجودة"}), 404

        data = request.get_json() or {}
        interface = data.get('interface', 'ether1')
        ip_range = data.get('ip_range')

        if not ip_range:
            return jsonify({"success": False, "message": "❌ يجب تحديد نطاق IP"}), 400
        if not validate_name(interface):
            return jsonify({"success": False, "error": "Invalid input", "field": "interface"}), 400
        if not validate_ip(ip_range, allow_cidr=True):
            return jsonify({"success": False, "error": "Invalid input", "field": "ip_range"}), 400
        if contains_forbidden_text(interface) or contains_forbidden_text(ip_range):
            return jsonify({"success": False, "error": "Invalid input"}), 400

        success, message, devices = DeviceService.discover_network_devices(
            network_id, interface, ip_range
        )
        write_audit_log(user_id, network_id, "scan_network", {"interface": interface, "ip_range": ip_range}, message, 200 if success else 400)

        status_code = 200 if success else 400

        return jsonify({
            "success": success,
            "message": message,
            "devices": devices or [],
            "device_count": len(devices) if devices else 0
        }), status_code

    except Exception as e:
        logger.error(f"❌ خطأ في المسح: {e}")
        return jsonify({"success": False, "message": f"❌ خطأ: {str(e)}"}), 500


@api_bp.route('/networks/<int:network_id>/scan/save', methods=['POST'])
@require_login
@require_role("admin", "distributor")
def save_scan_results(network_id):
    """حفظ نتائج المسح في الجدول مع فحص ping من MikroTik."""
    try:
        user_id = get_user_id()
        network = NetworkService.get_network_details(network_id, user_id)
        if not network:
            return jsonify({"success": False, "message": "❌ الشبكة غير موجودة"}), 404

        data = request.get_json() or {}
        devices = data.get('devices')
        if not devices or not isinstance(devices, list):
            return jsonify({"success": False, "message": "❌ قائمة الأجهزة مطلوبة"}), 400
        if len(devices) > 2000:
            return jsonify({"success": False, "error": "Invalid input", "field": "devices"}), 400

        success, message, count = DeviceService.commit_scan_results(network_id, devices)
        write_audit_log(user_id, network_id, "scan_save", {"count": len(devices)}, message, 200 if success else 400)
        status_code = 200 if success else 400
        return jsonify({
            "success": success,
            "message": message,
            "device_count": count
        }), status_code

    except Exception as e:
        logger.error(f"❌ خطأ في حفظ المسح: {e}")
        return jsonify({"success": False, "message": f"❌ خطأ: {str(e)}"}), 500


@api_bp.route('/networks/<int:network_id>/mikrotik/connect', methods=['POST'])
@require_login
@require_role("admin", "distributor")
def mikrotik_session_connect(network_id):
    """
    التحقق من بيانات MikroTik عبر SSH وتخزينها في الجلسة فقط (لا تُحفظ في قاعدة البيانات).
    """
    try:
        user_id = get_user_id()
        user = User.query.get(user_id)
        net = Network.query.get(network_id)
        if not net or not user or not user.can_manage_network(network_id):
            return jsonify({"success": False, "message": "❌ الشبكة غير موجودة"}), 404

        data = request.get_json() or {}
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        if not username or not password:
            return jsonify({"success": False, "message": "❌ اسم المستخدم وكلمة المرور مطلوبان"}), 400
        if not validate_name(username):
            return jsonify({"success": False, "error": "Invalid input", "field": "username"}), 400
        if contains_forbidden_text(username) or contains_forbidden_text(password):
            return jsonify({"success": False, "error": "Invalid input"}), 400

        if not net.mikrotik_ip:
            return jsonify({"success": False, "message": "❌ لم يُضبط عنوان الراوتر"}), 400

        port = int(net.mikrotik_port or 8728)
        svc = MikroTikService(
            host=net.mikrotik_ip,
            port=port,
            username=username,
            password=password,
            timeout=25,
            max_retries=1,
        )
        ok, info = svc.verify_ssh_login()
        if not ok:
            write_audit_log(user_id, network_id, "mikrotik_connect", {"username": username}, "failed", 400)
            return jsonify({
                "success": False,
                "error": "Command failed"
            }), 400

        bag = session.get("mikrotik_creds") or {}
        bag[str(network_id)] = {"username": username, "password": password}
        session["mikrotik_creds"] = bag
        session.modified = True

        net.mikrotik_onboarding_complete = True
        net.mikrotik_username = None
        net.mikrotik_password = None
        db.session.commit()

        ConnectionManager.close_connection(network_id)
        write_audit_log(user_id, network_id, "mikrotik_connect", {"username": username}, "success", 200)

        return jsonify({
            "success": True,
            "message": "Connected Successfully",
            "detail": info[:200] if info else ""
        })

    except Exception as e:
        logger.error(f"❌ خطأ في اتصال MikroTik: {e}")
        return jsonify({"success": False, "error": "Command failed"}), 500


@api_bp.route('/networks/<int:network_id>/devices/<device_ip>/ping', methods=['POST'])
@require_login
@require_role("admin", "distributor")
def ping_device(network_id, device_ip):
    """
    Ping جهاز.
    Query: source=local (افتراضي) من خادم التطبيق، أو source=router من MikroTik.
    """
    try:
        user_id = get_user_id()
        network = NetworkService.get_network_details(network_id, user_id)
        if not network:
            return jsonify({"success": False, "message": "❌ الشبكة غير موجودة"}), 404
        if not validate_ip(device_ip, allow_cidr=False):
            return jsonify({"success": False, "error": "Invalid input", "field": "device_ip"}), 400
        if contains_forbidden_text(device_ip):
            return jsonify({"success": False, "error": "Invalid input"}), 400

        source = (request.args.get("source") or "local").lower()
        from_router = source == "router"
        verbose = request.args.get("verbose", "").lower() in ("1", "true", "yes")

        if from_router and verbose:
            success, output = DeviceService.ping_device_router_verbose(
                network_id, device_ip
            )
        else:
            success, output = DeviceService.ping_device(
                network_id, device_ip, from_router=from_router
            )

        write_audit_log(user_id, network_id, "device_ping", {"device_ip": device_ip, "source": source}, "done", 200 if success else 400)
        return jsonify({
            "success": success,
            "status": "UP" if success else "DOWN",
            "output": output,
            "source": "router" if from_router else "local",
            "verbose": bool(from_router and verbose),
        })

    except Exception as e:
        logger.error("ping device failed: %s", e)
        return jsonify({"success": False, "error": "Command failed"}), 500


@api_bp.route('/networks/<int:network_id>/devices/refresh', methods=['POST'])
@require_login
@require_role("admin", "distributor")
def refresh_devices_status(network_id):
    """تحديث حالة جميع الأجهزة"""
    try:
        user_id = get_user_id()
        network = NetworkService.get_network_details(network_id, user_id)
        if not network:
            return jsonify({"success": False, "message": "❌ الشبكة غير موجودة"}), 404

        up_count, down_count = DeviceService.refresh_all_devices_status(network_id)
        write_audit_log(user_id, network_id, "refresh_devices_status", {}, f"up={up_count},down={down_count}", 200)

        return jsonify({
            "success": True,
            "message": f"✅ تم تحديث الأجهزة - Up: {up_count}, Down: {down_count}",
            "up": up_count,
            "down": down_count
        })

    except Exception as e:
        return jsonify({"success": False, "message": f"❌ خطأ: {str(e)}"}), 500


@api_bp.route('/networks/<int:network_id>/devices', methods=['POST'])
@require_login
@require_role("admin", "distributor")
def add_device(network_id):
    """إضافة جهاز يدويًا"""
    try:
        user_id = get_user_id()
        network = NetworkService.get_network_details(network_id, user_id)
        if not network:
            return jsonify({"success": False, "message": "❌ الشبكة غير موجودة"}), 404

        data = request.get_json() or {}
        if not validate_name(data.get('name', '')):
            return jsonify({"success": False, "error": "Invalid input", "field": "name"}), 400
        if not validate_ip(data.get('ip', ''), allow_cidr=False):
            return jsonify({"success": False, "error": "Invalid input", "field": "ip"}), 400
        mac = (data.get('mac') or '').strip()
        if mac and not validate_mac(mac):
            return jsonify({"success": False, "error": "Invalid input", "field": "mac"}), 400

        success, message = DeviceService.add_manual_device(
            network_id=network_id,
            name=data.get('name'),
            ip=data.get('ip'),
            mac=data.get('mac'),
            device_type=data.get('device_type', 'Unknown'),
            description=data.get('description'),
        )

        status_code = 201 if success else 400
        write_audit_log(user_id, network_id, "add_device", {"ip": data.get('ip')}, message, status_code)

        return jsonify({"success": success, "message": message}), status_code

    except Exception as e:
        logger.error(f"❌ خطأ في إضافة الجهاز: {e}")
        return jsonify({"success": False, "message": f"❌ خطأ: {str(e)}"}), 500


@api_bp.route('/devices/<int:device_id>', methods=['DELETE'])
@require_login
@require_role("admin", "distributor")
def delete_device(device_id):
    """حذف جهاز"""
    try:
        user_id = get_user_id()
        user = request.current_user
        device = Device.query.get(device_id)
        if not device:
            return jsonify({"success": False, "error": "Not found"}), 404
        if not user.can_manage_network(device.network_id):
            return jsonify({"success": False, "error": "Forbidden"}), 403
        success, message = DeviceService.delete_device(device_id)
        write_audit_log(user_id, device.network_id, "delete_device", {"device_id": device_id}, message, 200 if success else 400)

        if not success:
            return jsonify({"success": False, "message": message}), 400

        return jsonify({"success": True, "message": message})

    except Exception as e:
        return jsonify({"success": False, "message": f"❌ خطأ: {str(e)}"}), 500


# ==================== Health Check ====================

@api_bp.route('/health', methods=['GET'])
def health_check():
    """فحص صحة النظام"""
    return jsonify({
        "success": True,
        "message": "✅ النظام يعمل بشكل طبيعي",
        "timestamp": datetime.utcnow().isoformat()
    })


@api_bp.route('/status', methods=['GET'])
@require_login
def system_status():
    """حالة النظام"""
    try:
        from app.utils.connection_manager import ConnectionManager
        pool_size = ConnectionManager.get_pool_size()
        conn_status = ConnectionManager.get_connection_status()

        return jsonify({
            "success": True,
            "pool_size": pool_size,
            "connections": {str(k): v for k, v in conn_status.items()}
        })

    except Exception as e:
        return jsonify({"success": False, "message": f"❌ خطأ: {str(e)}"}), 500
