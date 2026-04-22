"""
🌐 Network Service - خدمات إدارة الشبكات
"""

import logging
from typing import List, Dict, Any, Tuple

from app.models.models import db, Network, Device, User
from app.utils.connection_manager import ConnectionManager

logger = logging.getLogger(__name__)


class NetworkService:
    """خدمات إدارة الشبكات"""

    @staticmethod
    def create_network(
        user_id: int,
        name: str,
        ip: str,
        port: int = 22,
        mikrotik_user: str = "",
        mikrotik_pass: str = "",
    ) -> Tuple[bool, str, int]:
        """
        إنشاء شبكة جديدة (عنوان الراوتر والمنفذ فقط — بيانات الدخول عبر الجلسة لاحقاً).

        العودة: (نجح, الرسالة, network_id)
        """
        try:
            if not name or not ip:
                return False, "❌ اسم الشبكة وعنوان IP للراوتر مطلوبان", 0

            # التحقق من عدم تكرار الشبكة
            existing = Network.query.filter_by(
                user_id=user_id,
                mikrotik_ip=ip
            ).first()

            if existing:
                return False, f"❌ الشبكة {ip} موجودة بالفعل", 0

            # إنشاء الشبكة
            network = Network(
                name=name,
                mikrotik_ip=ip,
                mikrotik_port=port,
                mikrotik_username=None,
                mikrotik_password=None,
                user_id=user_id,
                is_active=True,
            )

            db.session.add(network)
            db.session.commit()

            network_id = network.id

            return True, f"✅ تم إنشاء الشبكة: {name}", network_id

        except Exception as e:
            logger.error(f"❌ خطأ في إنشاء الشبكة: {e}")
            db.session.rollback()
            return False, f"❌ خطأ: {str(e)}", 0

    @staticmethod
    def get_user_networks(user_id: int) -> List[Dict[str, Any]]:
        """الحصول على شبكات المستخدم"""
        try:
            networks = Network.query.filter_by(user_id=user_id, is_active=True).all()
            result = []

            for network in networks:
                device_count = Device.query.filter_by(network_id=network.id).count()

                result.append({
                    "id": network.id,
                    "name": network.name,
                    "ip": network.mikrotik_ip,
                    "port": network.mikrotik_port,
                    "device_count": device_count,
                    "is_active": network.is_active,
                    "created_at": network.created_at.isoformat(),
                })

            return result

        except Exception as e:
            logger.error(f"❌ خطأ في جلب الشبكات: {e}")
            return []

    @staticmethod
    def get_network_details(network_id: int, user_id: int) -> Dict[str, Any]:
        """الحصول على تفاصيل شبكة معينة"""
        try:
            network = Network.query.filter_by(
                id=network_id,
                user_id=user_id
            ).first()

            if not network:
                return {}

            devices = Device.query.filter_by(network_id=network_id).all()
            up_devices = sum(1 for d in devices if d.status == "online")

            return {
                "id": network.id,
                "name": network.name,
                "ip": network.mikrotik_ip,
                "port": network.mikrotik_port,
                "total_devices": len(devices),
                "up_devices": up_devices,
                "down_devices": len(devices) - up_devices,
                "is_active": network.is_active,
                "created_at": network.created_at.isoformat(),
                "updated_at": network.updated_at.isoformat(),
            }

        except Exception as e:
            logger.error(f"❌ خطأ في جلب تفاصيل الشبكة: {e}")
            return {}

    @staticmethod
    def test_connection(network_id: int, user_id: int) -> Tuple[bool, str]:
        """اختبار الاتصال بشبكة"""
        try:
            network = Network.query.filter_by(
                id=network_id,
                user_id=user_id
            ).first()

            if not network:
                return False, "❌ الشبكة غير موجودة"

            conn = ConnectionManager.get_connection(network_id)
            if not conn:
                return False, f"❌ فشل الاتصال بـ {network.name}"

            success, info = conn.test_connection()

            if success:
                ConnectionManager.close_connection(network_id)
                return True, f"✅ الاتصال نجح:\n{info}"
            else:
                return False, f"❌ فشل الاتصال: {info}"

        except Exception as e:
            logger.error(f"❌ خطأ في اختبار الاتصال: {e}")
            return False, f"❌ خطأ: {str(e)}"

    @staticmethod
    def delete_network(network_id: int, user_id: int) -> Tuple[bool, str]:
        """حذف شبكة"""
        try:
            network = Network.query.filter_by(
                id=network_id,
                user_id=user_id
            ).first()

            if not network:
                return False, "❌ الشبكة غير موجودة"

            network_name = network.name

            # حذف جميع الأجهزة المرتبطة
            Device.query.filter_by(network_id=network_id).delete()

            # حذف الشبكة
            db.session.delete(network)
            db.session.commit()

            # قطع الاتصال إن وجد
            ConnectionManager.close_connection(network_id)

            logger.info(f"✅ تم حذف الشبكة: {network_name}")

            return True, f"✅ تم حذف الشبكة: {network_name}"

        except Exception as e:
            logger.error(f"❌ خطأ في حذف الشبكة: {e}")
            db.session.rollback()
            return False, f"❌ خطأ: {str(e)}"

    @staticmethod
    def get_network_interfaces(network_id: int, user_id: int) -> List[str]:
        """الحصول على واجهات الشبكة"""
        try:
            user = User.query.get(user_id)
            if not user:
                return []
            network = Network.query.get(network_id)

            if not network or not user.can_manage_network(network.id):
                return []

            conn = ConnectionManager.get_connection(network_id)
            if not conn:
                return []

            interfaces = conn.get_interfaces()
            ConnectionManager.close_connection(network_id)

            return interfaces

        except Exception as e:
            logger.error(f"❌ خطأ في جلب الواجهات: {e}")
            return []

    @staticmethod
    def get_network_health(network_id: int, user_id: int) -> str:
        """الحصول على معلومات صحة النظام"""
        try:
            network = Network.query.filter_by(
                id=network_id,
                user_id=user_id
            ).first()

            if not network:
                return "❌ الشبكة غير موجودة"

            conn = ConnectionManager.get_connection(network_id)
            if not conn:
                return "❌ لا يمكن الاتصال بـ MikroTik"

            health = conn.get_system_health()
            ConnectionManager.close_connection(network_id)

            return health

        except Exception as e:
            logger.error(f"❌ خطأ في جلب صحة النظام: {e}")
            return f"❌ خطأ: {str(e)}"
