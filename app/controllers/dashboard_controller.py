"""
Dashboard Controllers - Dashboard views و APIs
"""
from flask import Blueprint, render_template, jsonify, session, request
from datetime import datetime
from app.models.models import Device, DeviceStatusLog, Network, User, db
from app.utils.security import (
    RouterCommandQueue,
    contains_forbidden_text,
    enforce_rate_limit,
    validate_id,
    validate_ip,
    write_audit_log,
)
import logging
import threading
import time
import ipaddress
import re

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')
dashboard_api = Blueprint('dashboard_api', __name__, url_prefix='/api/dashboard')
live_api = Blueprint('live_api', __name__)
logger = logging.getLogger(__name__)
_live_cache = {}
_live_cache_lock = threading.Lock()
_router_queue = RouterCommandQueue()


def require_login(f):
    """ديكوريتور للتحقق من تسجيل الدخول"""
    def wrapper(*args, **kwargs):
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({"success": False, "message": "يجب تسجيل الدخول"}), 401
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper


# ==================== Views ====================

@dashboard_bp.route('/')
@require_login
def dashboard():
    """عرض لوحة التحكم الرئيسية"""
    from app.models.models import User
    user = User.query.get(session.get('user_id'))

    # التحقق من الصلاحيات
    if user.is_admin():
        # الإدارة يرون كل شيء
        networks = Network.query.all()
    else:
        networks = user.get_networks()

    current_network = networks[0] if networks else None
    return render_template('dashboard.html', user=user, networks=networks, current_network=current_network)


@dashboard_bp.route('/test-live')
@require_login
def test_live():
    """صفحة اختبار البيانات المباشرة"""
    return render_template('test_live.html')


# ==================== Dashboard APIs ====================

@dashboard_api.route('/status', methods=['GET'])
@require_login
def get_dashboard_status():
    """الحصول على حالة لوحة التحكم الحالية"""
    logger.info("تم استدعاء get_dashboard_status")
    try:
        user = User.query.get(session.get('user_id'))
        logger.info(f"المستخدم: {user.username}, الدور: {user.role}")

        # الحصول على الشبكات المسموحة
        if user.is_admin():
            networks = Network.query.all()
        else:
            networks = user.networks

        # حساب الإحصائيات
        total_devices = sum(len(network.devices) for network in networks)
        online_count = sum(
            1 for network in networks
            for device in network.devices
            if device.status == 'online'
        )
        warning_count = sum(
            1 for network in networks
            for device in network.devices
            if device.status == 'warning'
        )
        offline_count = sum(
            1 for network in networks
            for device in network.devices
            if device.status == 'offline'
        )

        # حساب التنبيهات
        alert_count = sum(network.get_alerts_count() for network in networks)

        # الحصول على الأجهزة الأخيرة
        devices_data = []
        for network in networks:
            for device in network.devices[:5]:  # 5 أجهزة من كل شبكة
                devices_data.append({
                    'id': device.id,
                    'name': device.name,
                    'ip_address': device.ip_address,
                    'mac_address': device.mac_address,
                    'device_type': device.device_type,
                    'description': device.description,
                    'ip': device.ip_address,
                    'mac': device.mac_address,
                    'type': device.device_type,
                    'status': device.status,
                    'ping': getattr(device, 'last_ping', 0) or 0,
                    'last_check': device.last_updated.isoformat() if device.last_updated else None,
                    'network_name': network.name
                })

        # ترتيب حسب آخر تحديث
        devices_data.sort(key=lambda x: x.get('last_check', ''), reverse=True)
        devices_data = devices_data[:10]  # أحدث 10 أجهزة

        # الحصول على حالة الإنترنت من MikroTik
        internet_status = 'unknown'
        internet_message = 'جاري التحقق...'
        hotspot_users = 0
        mikrotik_identity = None
        wan_interface = None
        wan_rx_mbps = None
        wan_tx_mbps = None
        wan_traffic_status = None
        wan_traffic_message = None

        try:
            for network in networks:
                try:
                    snapshot = _cached_router_snapshot(network)
                    internet_status = snapshot.get("internet_status", "unknown")
                    internet_message = snapshot.get("internet_message", "جاري التحقق...")
                    hotspot_users = snapshot.get("hotspot_users", 0)
                    mikrotik_identity = snapshot.get("mikrotik_identity")
                    wan_interface = snapshot.get("wan_interface")
                    wan_rx_mbps = snapshot.get("wan_rx_mbps")
                    wan_tx_mbps = snapshot.get("wan_tx_mbps")
                    wan_traffic_status = snapshot.get("wan_traffic_status")
                    wan_traffic_message = snapshot.get("wan_traffic_message")
                    break
                except Exception as e:
                    logger.warning(f"فشل التحقق من الإنترنت للشبكة {network.id}: {e}")
                    continue
        except Exception as e:
            logger.error(f"خطأ في التحقق من حالة الإنترنت: {e}")

        logger.info(
            f"إرجاع البيانات: hotspot_users={hotspot_users}, identity={mikrotik_identity}, wan={wan_interface}"
        )
        return jsonify({
            'success': True,
            'timestamp': datetime.utcnow().isoformat(),
            'online_count': online_count,
            'total_count': total_devices,
            'alerts_count': alert_count,
            'warning_count': warning_count,
            'offline_count': offline_count,
            'hotspot_users': hotspot_users,
            'hotspot_active_users': hotspot_users,
            'mikrotik_identity': mikrotik_identity or 'غير محدد',
            'wan_interface': wan_interface,
            'wan_rx_mbps': wan_rx_mbps,
            'wan_tx_mbps': wan_tx_mbps,
            'wan_traffic_status': wan_traffic_status,
            'wan_traffic_message': wan_traffic_message,
            'network_status': 'healthy' if alert_count == 0 else 'warning',
            'internet_status': internet_status,
            'internet_message': internet_message,
            'devices': devices_data,
            'networks_count': len(networks)
        })
    except Exception as e:
        logger.error(f"خطأ في الحصول على حالة Dashboard: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e),
            'online_count': 0,
            'total_count': 0,
            'alerts_count': 0
        }), 500


def _dashboard_resolve_network(user, network_id=None):
    if not network_id:
        if user.role == 'admin':
            return Network.query.first()
        return user.networks[0] if user.networks else None
    net = Network.query.get(network_id)
    if net and user.can_manage_network(net.id):
        return net
    return None


def _is_valid_ip(ip_value: str) -> bool:
    try:
        ipaddress.ip_address((ip_value or "").strip())
        return True
    except Exception:
        return False


def _cached_router_snapshot(network) -> dict:
    cache_key = f"router_snapshot:{network.id}"
    now = time.time()
    with _live_cache_lock:
        current = _live_cache.get(cache_key)
        if current and (now - current["ts"] < 15):
            return current["data"]

    from app.utils.connection_manager import ConnectionManager

    conn = ConnectionManager.get_connection(network.id)
    if not conn:
        data = {
            "internet_status": "unknown",
            "internet_message": "لا يوجد اتصال بالراوتر",
            "hotspot_users": 0,
            "mikrotik_identity": "غير محدد",
            "wan_interface": None,
            "wan_rx_mbps": None,
            "wan_tx_mbps": None,
            "wan_traffic_status": "no_data",
            "wan_traffic_message": "لا يوجد اتصال",
        }
    else:
        internet_ok, internet_msg = conn.get_internet_status()
        hotspot_data = conn.get_hotspot_users()
        wan = conn.get_wan_traffic_rates(getattr(network, "public_wan_ip", None) or "")
        data = {
            "internet_status": "online" if internet_ok else "offline",
            "internet_message": internet_msg,
            "hotspot_users": int(
                hotspot_data.get("active_sessions")
                or hotspot_data.get("total_users")
                or 0
            ),
            "mikrotik_identity": conn.get_identity() or "غير محدد",
            "wan_interface": wan.get("interface"),
            "wan_rx_mbps": float(wan["rx_mbps"]) if wan.get("rx_mbps") is not None else None,
            "wan_tx_mbps": float(wan["tx_mbps"]) if wan.get("tx_mbps") is not None else None,
            "wan_traffic_status": wan.get("status"),
            "wan_traffic_message": wan.get("error"),
        }

    with _live_cache_lock:
        _live_cache[cache_key] = {"ts": now, "data": data}
    return data


@dashboard_api.route('/router-live', methods=['GET'])
@require_login
def get_router_live_widgets():
    """DHCP clients + System health (بيانات حية من الراوتر)."""
    try:
        user = User.query.get(session.get('user_id'))
        network_id = request.args.get('network_id', type=int)
        network = _dashboard_resolve_network(user, network_id)
        if not network:
            return jsonify({'success': False, 'message': 'لا توجد شبكة'}), 404

        from app.utils.connection_manager import ConnectionManager

        conn = ConnectionManager.get_connection(network.id)
        if not conn:
            return jsonify({
                'success': False,
                'message': 'لا يوجد اتصال بالراوتر',
                'dhcp_clients': [],
                'health': {},
            })

        return jsonify({
            'success': True,
            'dhcp_clients': conn.get_dhcp_clients_rows(),
            'health': conn.get_system_health_details(),
        })
    except Exception as e:
        logger.error(f"router-live: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@dashboard_api.route('/router-logs', methods=['GET'])
@require_login
def get_router_logs_api():
    """آخر سطور من /log print على الراوتر."""
    try:
        user = User.query.get(session.get('user_id'))
        network_id = request.args.get('network_id', type=int)
        limit = request.args.get('limit', 45, type=int)
        network = _dashboard_resolve_network(user, network_id)
        if not network:
            return jsonify({'success': False, 'message': 'لا توجد شبكة'}), 404

        from app.utils.connection_manager import ConnectionManager

        conn = ConnectionManager.get_connection(network.id)
        if not conn:
            return jsonify({
                'success': False,
                'message': 'لا يوجد اتصال بالراوتر',
                'lines': [],
                'entries': [],
            })

        lines = conn.get_router_log_lines(limit=limit)
        entries = conn.get_router_log_entries(limit=limit)
        return jsonify({'success': True, 'lines': lines, 'entries': entries})
    except Exception as e:
        logger.error(f"router-logs: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@dashboard_api.route('/devices', methods=['GET'])
@require_login
def get_dashboard_devices():
    """الحصول على قائمة الأجهزة"""
    try:
        user = User.query.get(session.get('user_id'))

        # الحصول على الشبكات المسموحة
        if user.is_admin():
            networks = Network.query.all()
        else:
            networks = user.networks

        devices_data = []
        for network in networks:
            for device in network.devices:
                devices_data.append({
                    'id': device.id,
                    'name': device.name,
                    'ip_address': device.ip_address,
                    'mac_address': device.mac_address,
                    'device_type': device.device_type,
                    'description': device.description,
                    'ip': device.ip_address,
                    'mac': device.mac_address,
                    'type': device.device_type,
                    'status': device.status,
                    'ping': getattr(device, 'last_ping', 0) or 0,
                    'last_check': device.last_updated.isoformat() if device.last_updated else None,
                    'network_name': network.name,
                    'network_id': network.id
                })

        return jsonify({
            'success': True,
            'devices': devices_data,
            'total': len(devices_data)
        })
    except Exception as e:
        logger.error(f"خطأ في جلب الأجهزة: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@dashboard_api.route('/alerts', methods=['GET'])
@require_login
def get_dashboard_alerts():
    """الحصول على التنبيهات"""
    try:
        limit = request.args.get('limit', 10, type=int)
        alerts = DeviceStatusLog.query.filter_by(status='warning').order_by(
            DeviceStatusLog.created_at.desc()
        ).limit(limit).all()
        
        alerts_data = [
            {
                'id': a.id,
                'device_id': a.device_id,
                'status': a.status,
                'previous_status': a.previous_status,
                'timestamp': a.created_at.isoformat(),
            }
            for a in alerts
        ]
        
        return jsonify({
            'success': True,
            'alerts': alerts_data,
            'total': len(alerts_data)
        })
    except Exception as e:
        logger.error(f"خطأ في جلب التنبيهات: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@dashboard_api.route('/traffic', methods=['GET'])
@require_login
def get_traffic_data():
    """الحصول على بيانات حركة المرور الحقيقية من MikroTik"""
    try:
        user = User.query.get(session.get('user_id'))
        logger.info(f"get_traffic_data called by user_id={user.id} username={user.username} role={user.role}")

        # الحصول على الشبكة الأولى للمستخدم (أو الشبكة المحددة)
        network_id = request.args.get('network_id', type=int)
        if not network_id:
            if user.role == 'admin':
                network = Network.query.first()
            else:
                network = user.networks[0] if user.networks else None
        else:
            network = Network.query.get(network_id)
            if not network or not user.can_manage_network(network.id):
                logger.info(f"traffic access denied for user_id={user.id} to network_id={network_id}")
                return jsonify({'success': False, 'message': 'غير مصرح لك بالوصول لهذه الشبكة'}), 403

        logger.info(f"selected network for traffic: {network.id if network else None} name={network.name if network else None} ip={network.mikrotik_ip if network else None} port={network.mikrotik_port if network else None} username={network.mikrotik_username if network else None}")

        if not network:
            return jsonify({
                'success': False,
                'message': 'لا توجد شبكات متاحة',
                'labels': ['12:00', '12:30', '13:00', '13:30', '14:00', '14:30', '15:00'],
                'download': [0, 0, 0, 0, 0, 0, 0],
                'upload': [0, 0, 0, 0, 0, 0, 0]
            })

        # محاولة الاتصال بـ MikroTik والحصول على البيانات
        try:
            from app.services.mikrotik_service import MikroTikService
            from app.utils.connection_manager import ConnectionManager

            # استخدام ConnectionManager للحصول على اتصال آمن
            mikrotik_conn = ConnectionManager.get_connection(network.id)
            if not mikrotik_conn:
                return jsonify({
                    'success': False,
                    'message': 'لا يمكن الاتصال بـ MikroTik',
                    'labels': ['12:00', '12:30', '13:00', '13:30', '14:00', '14:30', '15:00'],
                    'download': [0, 0, 0, 0, 0, 0, 0],
                    'upload': [0, 0, 0, 0, 0, 0, 0]
                })

            # جلب بيانات حركة المرور
            traffic_data = mikrotik_conn.get_network_traffic(
                getattr(network, "public_wan_ip", None) or None
            )

            if traffic_data['success']:
                return jsonify({
                    'success': True,
                    'labels': traffic_data.get('labels') or [],
                    'download': traffic_data.get('download') or [],
                    'upload': traffic_data.get('upload') or [],
                    'current_upload': traffic_data.get('current_upload', 0),
                    'current_download': traffic_data.get('current_download', 0),
                    'wan_interface': traffic_data.get('wan_interface'),
                    'wan_traffic_status': traffic_data.get('wan_traffic_status', 'ok'),
                })
            else:
                return jsonify({
                    'success': False,
                    'message': traffic_data.get('error', 'فشل في جلب البيانات'),
                    'wan_traffic_status': traffic_data.get('wan_traffic_status', 'no_data'),
                    'wan_interface': traffic_data.get('wan_interface'),
                    'labels': traffic_data.get('labels') or [],
                    'download': traffic_data.get('download') or [],
                    'upload': traffic_data.get('upload') or [],
                })

        except Exception as e:
            logger.error(f"خطأ في الاتصال بـ MikroTik: {str(e)}")
            # إرجاع بيانات افتراضية في حالة الخطأ
            return jsonify({
                'success': False,
                'message': f'خطأ في الاتصال: {str(e)}',
                'labels': ['12:00', '12:30', '13:00', '13:30', '14:00', '14:30', '15:00'],
                'download': [0, 0, 0, 0, 0, 0, 0],
                'upload': [0, 0, 0, 0, 0, 0, 0]
            })

    except Exception as e:
        logger.error(f"خطأ في جلب بيانات حركة المرور: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@dashboard_api.route('/devices/add', methods=['POST'])
@require_login
def add_device_from_dashboard():
    """إضافة جهاز جديد"""
    try:
        user = User.query.get(session.get('user_id'))
        data = request.get_json()

        if not data.get('name') or not data.get('ip') or not data.get('network_id'):
            return jsonify({'success': False, 'message': 'اسم الجهاز و IP والشبكة اجباريان'}), 400

        # التحقق من صلاحية الوصول للشبكة
        network = Network.query.get(data['network_id'])
        if not network:
            return jsonify({'success': False, 'message': 'الشبكة غير موجودة'}), 404

        if not user.can_manage_network(network.id):
            return jsonify({'success': False, 'message': 'غير مصرح لك بإضافة أجهزة لهذه الشبكة'}), 403

        device = Device(
            name=data.get('name'),
            ip_address=data.get('ip'),
            mac_address=data.get('mac'),
            device_type=data.get('type', 'Unknown'),
            description=data.get('description'),
            network_id=data['network_id'],
            status='offline'
        )

        db.session.add(device)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'تم إضافة الجهاز بنجاح',
            'device_id': device.id
        }), 201
    except Exception as e:
        logger.error(f"خطأ في إضافة الجهاز: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@dashboard_api.route('/refresh', methods=['POST'])
@require_login
def refresh_all():
    """تحديث جميع البيانات"""
    try:
        devices = Device.query.all()
        online = sum(1 for d in devices if d.status == 'online')
        
        return jsonify({
            'success': True,
            'message': f'تم تحديث البيانات - {online} أجهزة متصلة',
            'timestamp': datetime.utcnow().isoformat()
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== Distributor APIs ====================

network_api = Blueprint('network_api', __name__, url_prefix='/api/network')


def require_distributor(f):
    """ديكوريتور للتحقق من أن المستخدم موزع"""
    def wrapper(*args, **kwargs):
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({"success": False, "message": "يجب تسجيل الدخول"}), 401

        from app.models.models import User
        user = User.query.get(user_id)
        if not user or user.role != 'distributor':
            return jsonify({"success": False, "message": "غير مصرح لك بالوصول"}), 403

        return f(user, *args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper


@network_api.route('/users', methods=['GET'])
@require_distributor
def get_network_users(user):
    """الحصول على مستخدمي الشبكة للموزع"""
    try:
        # الحصول على الشبكات التي يديرها الموزع
        networks = user.networks
        network_ids = [n.id for n in networks]

        # الحصول على المستخدمين المرتبطين بهذه الشبكات
        from app.models.models import User as UserModel
        users = UserModel.query.filter(
            UserModel.network_id.in_(network_ids),
            UserModel.role == 'user'  # فقط المستخدمين العاديين
        ).all()

        users_data = []
        for u in users:
            users_data.append({
                'id': u.id,
                'username': u.username,
                'email': u.email,
                'full_name': u.full_name,
                'is_active': u.is_active,
                'created_at': u.created_at.isoformat() if u.created_at else None,
                'network_name': u.network.name if u.network else None
            })

        return jsonify({
            'success': True,
            'users': users_data
        })

    except Exception as e:
        logger.error(f"خطأ في الحصول على مستخدمي الشبكة: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@network_api.route('/add-user', methods=['POST'])
@require_distributor
def add_network_user(user):
    """إضافة مستخدم جديد للشبكة"""
    try:
        data = request.get_json()

        if not data.get('username') or not data.get('email') or not data.get('password'):
            return jsonify({'success': False, 'message': 'اسم المستخدم والبريد الإلكتروني وكلمة المرور اجباريان'}), 400

        from app.models.models import User as UserModel, Network

        # التحقق من وجود المستخدم
        existing_user = UserModel.query.filter(
            (UserModel.username == data['username']) | (UserModel.email == data['email'])
        ).first()

        if existing_user:
            return jsonify({'success': False, 'message': 'اسم المستخدم أو البريد الإلكتروني موجود مسبقاً'}), 400

        # الحصول على الشبكة الأولى للموزع (افتراضياً)
        network = user.networks[0] if user.networks else None
        if not network:
            return jsonify({'success': False, 'message': 'لا توجد شبكات مرتبطة بك'}), 400

        # إنشاء المستخدم الجديد
        new_user = UserModel(
            username=data['username'],
            email=data['email'],
            full_name=data.get('full_name', ''),
            role='user',
            network_id=network.id,
            is_active=True
        )
        new_user.set_password(data['password'])

        db.session.add(new_user)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'تم إضافة المستخدم بنجاح',
            'user_id': new_user.id
        }), 201

    except Exception as e:
        logger.error(f"خطأ في إضافة المستخدم: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@network_api.route('/users/<int:user_id>/status', methods=['PUT'])
@require_distributor
def toggle_user_status(user, user_id):
    """تغيير حالة المستخدم (تفعيل/تعطيل)"""
    try:
        from app.models.models import User as UserModel

        target_user = UserModel.query.get(user_id)
        if not target_user:
            return jsonify({'success': False, 'message': 'المستخدم غير موجود'}), 404

        # التحقق من أن المستخدم ينتمي لشبكة الموزع
        if target_user.network_id not in [n.id for n in user.networks]:
            return jsonify({'success': False, 'message': 'غير مصرح لك بإدارة هذا المستخدم'}), 403

        data = request.get_json()
        target_user.is_active = data.get('is_active', True)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': f'تم {"تفعيل" if target_user.is_active else "تعطيل"} المستخدم بنجاح'
        })

    except Exception as e:
        logger.error(f"خطأ في تحديث حالة المستخدم: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@network_api.route('/settings', methods=['GET'])
@require_distributor
def get_network_settings(user):
    """الحصول على إعدادات الشبكة"""
    try:
        logger.info(f"get_network_settings called by distributor user_id={user.id} username={user.username}")
        networks = user.get_networks()
        network = networks[0] if networks else None
        if not network:
            logger.info(f"no network found for distributor user_id={user.id}")
            return jsonify({'success': False, 'message': 'لا توجد شبكات مرتبطة بك'}), 404

        from flask import session

        creds_bag = session.get("mikrotik_creds") or {}
        has_session_creds = bool(
            creds_bag.get(str(network.id), {}).get("password")
        )

        logger.info(
            f"returning network settings for network_id={network.id} "
            f"onboarding={getattr(network, 'mikrotik_onboarding_complete', False)} "
            f"session_creds={has_session_creds}"
        )

        network_data = {
            'id': network.id,
            'name': network.name,
            'is_active': network.is_active,
            'mikrotik_ip': network.mikrotik_ip,
            'mikrotik_port': network.mikrotik_port,
            'public_wan_ip': getattr(network, 'public_wan_ip', None),
            'mikrotik_onboarding_complete': bool(
                getattr(network, 'mikrotik_onboarding_complete', False)
            ),
            'mikrotik_session_active': has_session_creds,
            'created_at': network.created_at.isoformat() if network.created_at else None
        }

        return jsonify({
            'success': True,
            'network': network_data
        })

    except Exception as e:
        logger.error(f"خطأ في الحصول على إعدادات الشبكة: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@network_api.route('/settings', methods=['PUT'])
@require_distributor
def update_network_settings(user):
    """تحديث إعدادات الشبكة"""
    try:
        # الحصول على الشبكة الأولى للموزع
        network = user.networks[0] if user.networks else None
        if not network:
            return jsonify({'success': False, 'message': 'لا توجد شبكات مرتبطة بك'}), 404

        data = request.get_json()

        # تحديث البيانات (بدون تخزين بيانات دخول MikroTik — تبقى في الجلسة فقط)
        network.name = data.get('name', network.name)
        network.is_active = data.get('is_active', network.is_active)
        network.mikrotik_ip = data.get('mikrotik_ip', network.mikrotik_ip)
        network.mikrotik_port = data.get('mikrotik_port', network.mikrotik_port)
        if 'public_wan_ip' in data:
            v = data.get('public_wan_ip')
            network.public_wan_ip = (v or '').strip() or None

        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'تم حفظ الإعدادات بنجاح'
        })

    except Exception as e:
        logger.error(f"خطأ في تحديث إعدادات الشبكة: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@dashboard_api.route('/network-identity', methods=['GET'])
@require_login
def get_network_identity():
    """الحصول على اسم الشبكة (Identity) من MikroTik"""
    try:
        user = User.query.get(session.get('user_id'))

        # الحصول على الشبكة المحددة أو الشبكة الأولى
        network_id = request.args.get('network_id', type=int)
        if not network_id:
            if user.role == 'admin':
                network = Network.query.first()
            else:
                network = user.networks[0] if user.networks else None
        else:
            network = Network.query.get(network_id)
            if not network or not user.can_manage_network(network.id):
                return jsonify({'success': False, 'message': 'غير مصرح لك بالوصول لهذه الشبكة'}), 403

        if not network:
            return jsonify({
                'success': False,
                'message': 'لا توجد شبكات متاحة',
                'identity': 'غير محدد'
            })

        # محاولة الاتصال بـ MikroTik والحصول على Identity
        try:
            from app.utils.connection_manager import ConnectionManager

            mikrotik_conn = ConnectionManager.get_connection(network.id)
            if not mikrotik_conn:
                return jsonify({
                    'success': False,
                    'message': 'لا يمكن الاتصال بـ MikroTik',
                    'identity': network.name  # استخدام اسم الشبكة من قاعدة البيانات كبديل
                })

            # جلب Identity من MikroTik
            identity = mikrotik_conn.get_identity()

            return jsonify({
                'success': True,
                'identity': identity,
                'network_name': network.name
            })

        except Exception as e:
            logger.error(f"خطأ في الاتصال بـ MikroTik: {str(e)}")
            return jsonify({
                'success': False,
                'message': f'خطأ في الاتصال: {str(e)}',
                'identity': network.name  # استخدام اسم الشبكة من قاعدة البيانات كبديل
            })

    except Exception as e:
        logger.error(f"خطأ في جلب اسم الشبكة: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@dashboard_api.route('/hotspot-users', methods=['GET'])
@require_login
def get_hotspot_users():
    """الحصول على عدد مستخدمي hotspot"""
    try:
        user = User.query.get(session.get('user_id'))
        logger.info(f"get_hotspot_users called by user_id={user.id} username={user.username} role={user.role}")

        # الحصول على الشبكة المحددة أو الشبكة الأولى
        network_id = request.args.get('network_id', type=int)
        if not network_id:
            if user.role == 'admin':
                network = Network.query.first()
            else:
                network = user.networks[0] if user.networks else None
        else:
            network = Network.query.get(network_id)
            if not network or not user.can_manage_network(network.id):
                return jsonify({'success': False, 'message': 'غير مصرح لك بالوصول لهذه الشبكة'}), 403

        logger.info(f"selected network for hotspot_users: {network.id if network else None} name={network.name if network else None} ip={network.mikrotik_ip if network else None} port={network.mikrotik_port if network else None} username={network.mikrotik_username if network else None}")

        if not network:
            return jsonify({
                'success': False,
                'message': 'لا توجد شبكات متاحة',
                'hotspot_users': 0
            })

        # محاولة الاتصال بـ MikroTik والحصول على مستخدمي hotspot
        try:
            from app.utils.connection_manager import ConnectionManager

            mikrotik_conn = ConnectionManager.get_connection(network.id)
            if not mikrotik_conn:
                return jsonify({
                    'success': False,
                    'message': 'لا يمكن الاتصال بـ MikroTik',
                    'hotspot_users': 0
                })

            # جلب مستخدمي hotspot من MikroTik
            hotspot_data = mikrotik_conn.get_hotspot_users()

            active = int(
                hotspot_data.get('active_sessions')
                or hotspot_data.get('total_users')
                or 0
            )
            return jsonify({
                'success': True,
                'hotspot_users': active,
                'active_sessions': active,
                'registered_users': hotspot_data.get('registered_users', 0),
            })

        except Exception as e:
            logger.error(f"خطأ في الاتصال بـ MikroTik: {str(e)}")
            return jsonify({
                'success': False,
                'message': f'خطأ في الاتصال: {str(e)}',
                'hotspot_users': 0
            })

    except Exception as e:
        logger.error(f"خطأ في جلب مستخدمي hotspot: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@live_api.route('/update_data', methods=['GET'])
@require_login
def update_data():
    """Endpoint موحد للتحديث الدوري بدون refresh."""
    return get_dashboard_status()


@dashboard_api.route('/tools', methods=['POST'])
@require_login
def run_network_tools():
    """Ping/Traceroute مباشر من MikroTik."""
    try:
        limited, resp = enforce_rate_limit("write")
        if limited:
            return resp
        user = User.query.get(session.get('user_id'))
        data = request.get_json() or {}
        tool = (data.get('tool') or '').strip().lower()
        target_ip = (data.get('ip') or '').strip()
        network_id = data.get('network_id')

        if tool not in ('ping', 'traceroute'):
            return jsonify({'success': False, 'error': 'Invalid input', 'field': 'tool'}), 400
        if not validate_ip(target_ip, allow_cidr=False):
            return jsonify({'success': False, 'error': 'Invalid input', 'field': 'ip'}), 400
        if network_id is not None and not validate_id(network_id):
            return jsonify({'success': False, 'error': 'Invalid input', 'field': 'network_id'}), 400
        if contains_forbidden_text(target_ip):
            return jsonify({'success': False, 'error': 'Invalid input'}), 400

        network = _dashboard_resolve_network(user, network_id)
        if not network:
            return jsonify({'success': False, 'error': 'Forbidden'}), 403

        from app.utils.connection_manager import ConnectionManager
        conn = ConnectionManager.get_connection(network.id)
        if not conn:
            write_audit_log(user.id, network.id, "network_tool", {"tool": tool, "ip": target_ip}, "no_connection", 400)
            return jsonify({'success': False, 'error': 'Command failed'}), 400

        command = (
            f'/ping address={target_ip} count=4'
            if tool == "ping"
            else f'/tool traceroute address={target_ip}'
        )
        timeout_s = 20 if tool == "traceroute" else 10
        queued, queue_state, value = _router_queue.run(
            router_id=network.id,
            callable_=lambda: conn._run_routeros_ssh([command]),
            timeout_seconds=timeout_s,
            max_pending=5,
        )
        if not queued:
            code = 503 if queue_state == "QUEUE_FULL" else 500
            write_audit_log(user.id, network.id, "network_tool", {"tool": tool, "ip": target_ip}, queue_state, code)
            return jsonify({'success': False, 'error': 'Command failed'}), code
        ok, outs, err = value

        output = '\n'.join(outs or []).strip()
        if not ok:
            write_audit_log(user.id, network.id, "network_tool", {"tool": tool, "ip": target_ip}, "failed", 500)
            return jsonify({
                'success': False,
                'error': 'Command failed'
            }), 500

        write_audit_log(user.id, network.id, "network_tool", {"tool": tool, "ip": target_ip}, "success", 200)
        return jsonify({
            'success': True,
            'tool': tool,
            'ip': target_ip,
            'output': output or 'تم التنفيذ بنجاح'
        })
    except Exception as e:
        logger.error(f"tools api error: {e}")
        return jsonify({'success': False, 'error': 'Command failed'}), 500


@dashboard_api.route('/tools/dhcp-leases', methods=['GET'])
@require_login
def get_dhcp_leases_tool():
    """جلب DHCP Leases من MikroTik."""
    try:
        limited, resp = enforce_rate_limit("read")
        if limited:
            return resp
        user = User.query.get(session.get('user_id'))
        network_id = request.args.get('network_id', type=int)
        network = _dashboard_resolve_network(user, network_id)
        if not network:
            return jsonify({'success': False, 'error': 'Forbidden'}), 403
        from app.utils.connection_manager import ConnectionManager
        conn = ConnectionManager.get_connection(network.id)
        if not conn:
            write_audit_log(user.id, network.id, "dhcp_leases", {}, "no_connection", 400)
            return jsonify({'success': False, 'error': 'Command failed'}), 400
        rows = conn.get_dhcp_leases_rows(limit=300)
        write_audit_log(user.id, network.id, "dhcp_leases", {}, f"count={len(rows)}", 200)
        return jsonify({'success': True, 'leases': rows, 'count': len(rows)})
    except Exception as e:
        logger.error("dhcp leases tool error: %s", e)
        return jsonify({'success': False, 'error': 'Command failed'}), 500


@dashboard_api.route('/tools/dhcp-kick', methods=['POST'])
@require_login
def dhcp_kick_tool():
    """طرد lease حسب IP أو MAC."""
    try:
        limited, resp = enforce_rate_limit("write")
        if limited:
            return resp
        user = User.query.get(session.get('user_id'))
        data = request.get_json() or {}
        network_id = data.get('network_id')
        target_ip = (data.get('ip') or '').strip()
        target_mac = (data.get('mac') or '').strip()
        if network_id is not None and not validate_id(network_id):
            return jsonify({'success': False, 'error': 'Invalid input', 'field': 'network_id'}), 400
        if target_ip and not validate_ip(target_ip, allow_cidr=False):
            return jsonify({'success': False, 'error': 'Invalid input', 'field': 'ip'}), 400
        if target_mac and not re.match(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$", target_mac):
            return jsonify({'success': False, 'error': 'Invalid input', 'field': 'mac'}), 400
        if not target_ip and not target_mac:
            return jsonify({'success': False, 'error': 'Invalid input', 'field': 'ip'}), 400
        if contains_forbidden_text(target_ip) or contains_forbidden_text(target_mac):
            return jsonify({'success': False, 'error': 'Invalid input'}), 400

        network = _dashboard_resolve_network(user, network_id)
        if not network:
            return jsonify({'success': False, 'error': 'Forbidden'}), 403
        from app.utils.connection_manager import ConnectionManager
        conn = ConnectionManager.get_connection(network.id)
        if not conn:
            write_audit_log(user.id, network.id, "dhcp_kick", {"ip": target_ip, "mac": target_mac}, "no_connection", 400)
            return jsonify({'success': False, 'error': 'Command failed'}), 400

        queued, queue_state, value = _router_queue.run(
            router_id=network.id,
            callable_=lambda: conn.remove_dhcp_lease(ip=target_ip, mac=target_mac),
            timeout_seconds=10,
            max_pending=5,
        )
        if not queued:
            code = 503 if queue_state == "QUEUE_FULL" else 500
            write_audit_log(user.id, network.id, "dhcp_kick", {"ip": target_ip, "mac": target_mac}, queue_state, code)
            return jsonify({'success': False, 'error': 'Command failed'}), code
        ok, msg = value
        write_audit_log(user.id, network.id, "dhcp_kick", {"ip": target_ip, "mac": target_mac}, "success" if ok else "failed", 200 if ok else 500)
        if not ok:
            return jsonify({'success': False, 'error': 'Command failed'}), 500
        return jsonify({'success': True, 'message': msg})
    except Exception as e:
        logger.error("dhcp kick tool error: %s", e)
        return jsonify({'success': False, 'error': 'Command failed'}), 500
