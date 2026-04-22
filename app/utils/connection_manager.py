"""
🔌 Connection Manager - إدارة الاتصالات بـ MikroTik
نمط: Request-based + Connection Pooling
"""

from typing import Dict, Optional, Tuple
import threading
import logging
from app.models.models import Network
from app.services.mikrotik_service import MikroTikService

logger = logging.getLogger(__name__)


def _session_mikrotik_credentials(network_id: int) -> Tuple[Optional[str], Optional[str]]:
    """بيانات الدخول المؤقتة من جلسة Flask فقط (لا تُقرأ من قاعدة البيانات)."""
    try:
        from flask import has_request_context, session

        if not has_request_context():
            return None, None
        bag = session.get("mikrotik_creds") or {}
        entry = bag.get(str(network_id))
        if not entry:
            return None, None
        u = (entry.get("username") or "").strip()
        p = entry.get("password") or ""
        if u and p:
            return u, p
    except Exception:
        pass
    return None, None


class ConnectionManager:
    """
    مدير الاتصالات بـ MikroTik
    
    الميزات:
    - Request-based: فتح ← تنفيذ ← إغلاق
    - Connection pooling: حفظ اتصالات قابلة للإعادة
    - Thread-safe: آمن للاستخدام المتزامن
    - Smart reconnect: إعادة اتصال تلقائية عند الحاجة
    """

    # متغيرات class level
    _connections: Dict[int, MikroTikService] = {}
    _lock = threading.RLock()
    _max_pool_size = 10

    @classmethod
    def get_connection(cls, network_id: int) -> Optional[MikroTikService]:
        """
        الحصول على اتصال بـ MikroTik
        
        المنطق:
        1. التحقق من وجود اتصال في الـ Pool
        2. إذا لم يوجد → إنشاء اتصال جديد
        3. اختبار الاتصال
        4. إذا فشل → إعادة إنشاء
        """
        with cls._lock:
            # التحقق من وجود اتصال في الـ Pool
            if network_id in cls._connections:
                conn = cls._connections[network_id]
                if conn.is_connected():
                    logger.info(f"✅ اتصال موجود وجاهز للشبكة: {network_id}")
                    return conn
                else:
                    logger.warning(f"⚠️ اتصال قديم للشبكة {network_id}، حذفه...")
                    del cls._connections[network_id]

            # إنشاء اتصال جديد
            network = Network.query.get(network_id)
            if not network:
                logger.error(f"❌ الشبكة {network_id} غير موجودة")
                return None

            try:
                sess_user, sess_pass = _session_mikrotik_credentials(network_id)
                if not sess_user or not sess_pass:
                    logger.error(
                        "❌ لا توجد بيانات MikroTik في الجلسة للشبكة %s — سجّل الدخول للراوتر من الإعدادات",
                        network_id,
                    )
                    return None

                conn = MikroTikService(
                    host=network.mikrotik_ip,
                    port=network.mikrotik_port,
                    username=sess_user,
                    password=sess_pass,
                    timeout=20,
                    max_retries=2,
                )

                # اختبار الاتصال
                success, info = conn.test_connection()
                if not success:
                    logger.error(f"❌ فشل الاتصال بـ {network.name}: {info}")
                    return None

                # حفظ في الـ Pool
                if len(cls._connections) >= cls._max_pool_size:
                    # حذف أقدم اتصال
                    oldest_id = next(iter(cls._connections))
                    cls.close_connection(oldest_id)

                cls._connections[network_id] = conn
                logger.info(f"✅ اتصال جديد للشبكة: {network.name}")

                return conn

            except Exception as e:
                logger.error(f"❌ خطأ في إنشاء اتصال: {e}")
                return None

    @classmethod
    def close_connections_for_network_ids(cls, network_ids) -> None:
        """إغلاق اتصالات MikroTik لعدة شبكات (مثلاً عند تسجيل الخروج)."""
        for nid in network_ids or []:
            cls.close_connection(int(nid))

    @classmethod
    def close_connection(cls, network_id: int) -> None:
        """إغلاق اتصال معين"""
        with cls._lock:
            if network_id in cls._connections:
                try:
                    cls._connections[network_id]._disconnect()
                    del cls._connections[network_id]
                    logger.info(f"❌ قطع الاتصال من الشبكة: {network_id}")
                except Exception as e:
                    logger.error(f"❌ خطأ أثناء إغلاق الاتصال: {e}")

    @classmethod
    def close_all(cls) -> None:
        """إغلاق جميع الاتصالات"""
        with cls._lock:
            for network_id in list(cls._connections.keys()):
                cls.close_connection(network_id)
            logger.info(f"❌ تم إغلاق جميع الاتصالات")

    @classmethod
    def get_connection_status(cls) -> Dict[int, bool]:
        """الحصول على حالة جميع الاتصالات"""
        with cls._lock:
            status = {}
            for network_id, conn in cls._connections.items():
                status[network_id] = conn.is_connected()
            return status

    @classmethod
    def get_pool_size(cls) -> int:
        """حجم الـ Connection Pool الحالي"""
        with cls._lock:
            return len(cls._connections)

    @classmethod
    def clear_pool(cls) -> None:
        """تنظيف Pool من الاتصالات الميتة"""
        with cls._lock:
            dead_connections = []
            for network_id, conn in cls._connections.items():
                if not conn.is_connected():
                    dead_connections.append(network_id)

            for network_id in dead_connections:
                cls.close_connection(network_id)

            logger.info(f"✅ تم تنظيف {len(dead_connections)} اتصالات ميتة")


# Context Manager للاستخدام الآمن
class MikroTikContext:
    """
    Context Manager لاستخدام آمن للاتصال
    
    مثال:
    ```python
    with MikroTikContext(network_id) as conn:
        result = conn.mikrotik_ping("192.168.1.1")
    ```
    """

    def __init__(self, network_id: int):
        self.network_id = network_id
        self.conn: Optional[MikroTikService] = None

    def __enter__(self) -> Optional[MikroTikService]:
        self.conn = ConnectionManager.get_connection(self.network_id)
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        # في نمط Request-based، نتركه في الـ Pool
        # ولا نقطعه مباشرة بل نتركه لـ pool manager
        pass


# Decorator للدوال التي تحتاج إلى اتصال MikroTik
def requires_mikrotik_connection(network_id_param: str = "network_id"):
    """
    ديكوريتور للتحقق من اتصال MikroTik
    
    مثال:
    @requires_mikrotik_connection()
    def scan_network(network_id):
        pass
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            network_id = kwargs.get(network_id_param)

            if not network_id:
                # محاولة الحصول من الـ args
                import inspect
                sig = inspect.signature(func)
                params = list(sig.parameters.keys())
                if network_id_param in params:
                    idx = params.index(network_id_param)
                    if idx < len(args):
                        network_id = args[idx]

            if not network_id:
                raise ValueError(f"❌ يجب توفير {network_id_param}")

            conn = ConnectionManager.get_connection(network_id)
            if not conn:
                raise ConnectionError(f"❌ لا يمكن الاتصال بالشبكة: {network_id}")

            # تمرير الاتصال في kwargs
            kwargs["_conn"] = conn

            return func(*args, **kwargs)

        return wrapper
    return decorator
