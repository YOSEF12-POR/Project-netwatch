"""
Admin Controllers - إدارة المستخدمين والشبكات
"""
from flask import Blueprint, render_template, jsonify, request, session, flash, redirect, url_for
from app.models.models import db, User, Network, Device, AuditLog
from app.services.auth_service import AuthService
from werkzeug.security import generate_password_hash
from datetime import datetime
import logging

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')
logger = logging.getLogger(__name__)

def require_admin(f):
    """ديكوريتور للتحقق من صلاحية الإدارة"""
    def wrapper(*args, **kwargs):
        user_id = session.get('user_id')
        if not user_id:
            flash('يجب تسجيل الدخول', 'danger')
            return redirect(url_for('auth.login'))

        user = User.query.get(user_id)
        if not user or not user.is_admin():
            flash('غير مصرح لك بالوصول إلى هذه الصفحة', 'danger')
            return redirect(url_for('dashboard.dashboard'))

        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper

# ==================== Admin Views ====================

@admin_bp.route('/')
@require_admin
def admin_dashboard():
    """لوحة تحكم الإدارة"""
    users_count = User.query.count()
    networks_count = Network.query.count()
    devices_count = Device.query.count()
    online_devices = Device.query.filter_by(status='online').count()

    recent_users = User.query.order_by(User.created_at.desc()).limit(5).all()
    recent_networks = Network.query.order_by(Network.created_at.desc()).limit(5).all()

    return render_template('admin/dashboard.html',
                         users_count=users_count,
                         networks_count=networks_count,
                         devices_count=devices_count,
                         online_devices=online_devices,
                         recent_users=recent_users,
                         recent_networks=recent_networks)

@admin_bp.route('/users')
@require_admin
def users_management():
    """إدارة المستخدمين"""
    users = User.query.all()
    return render_template('admin/users.html', users=users)

@admin_bp.route('/networks')
@require_admin
def networks_management():
    """إدارة الشبكات"""
    networks = Network.query.all()
    return render_template('admin/networks.html', networks=networks)

# ==================== User Management APIs ====================

@admin_bp.route('/users/add', methods=['POST'])
@require_admin
def add_user():
    """إضافة مستخدم جديد"""
    try:
        data = request.get_json()

        # التحقق من البيانات المطلوبة
        required_fields = ['username', 'email', 'password', 'role']
        for field in required_fields:
            if not data.get(field):
                return jsonify({'success': False, 'message': f'حقل {field} مطلوب'}), 400

        # التحقق من صحة الدور
        if data['role'] not in ['admin', 'distributor']:
            return jsonify({'success': False, 'message': 'دور غير صحيح'}), 400

        # إنشاء المستخدم
        success, message = AuthService.register_user(
            data['username'],
            data['email'],
            data['password'],
            data['password'],  # confirm password
            data['role'],
            data.get('full_name'),
            data.get('phone'),
            data.get('company')
        )

        if success:
            # تسجيل في سجل الأنشطة
            admin_user = User.query.get(session['user_id'])
            audit_log = AuditLog(
                user_id=admin_user.id,
                action=f"إضافة مستخدم جديد: {data['username']}",
                details=f"الدور: {data['role']}"
            )
            db.session.add(audit_log)
            db.session.commit()

            return jsonify({'success': True, 'message': message}), 201
        else:
            return jsonify({'success': False, 'message': message}), 400

    except Exception as e:
        logger.error(f"خطأ في إضافة المستخدم: {str(e)}")
        return jsonify({'success': False, 'message': 'خطأ في الخادم'}), 500

@admin_bp.route('/users/<int:user_id>', methods=['PUT'])
@require_admin
def update_user(user_id):
    """تحديث بيانات المستخدم"""
    try:
        user = User.query.get_or_404(user_id)
        data = request.get_json()

        # تحديث البيانات
        if 'username' in data:
            user.username = data['username']
        if 'email' in data:
            user.email = data['email']
        if 'role' in data and data['role'] in ['admin', 'distributor']:
            user.role = data['role']
        if 'full_name' in data:
            user.full_name = data['full_name']
        if 'phone' in data:
            user.phone = data['phone']
        if 'company' in data:
            user.company = data['company']
        if 'is_active' in data:
            user.is_active = data['is_active']

        user.updated_at = datetime.utcnow()
        db.session.commit()

        # تسجيل في سجل الأنشطة
        admin_user = User.query.get(session['user_id'])
        audit_log = AuditLog(
            user_id=admin_user.id,
            action=f"تحديث بيانات المستخدم: {user.username}",
            details=f"التغييرات: {', '.join(data.keys())}"
        )
        db.session.add(audit_log)
        db.session.commit()

        return jsonify({'success': True, 'message': 'تم تحديث المستخدم بنجاح'})

    except Exception as e:
        logger.error(f"خطأ في تحديث المستخدم: {str(e)}")
        return jsonify({'success': False, 'message': 'خطأ في الخادم'}), 500

@admin_bp.route('/users/<int:user_id>', methods=['DELETE'])
@require_admin
def delete_user(user_id):
    """حذف المستخدم"""
    try:
        user = User.query.get_or_404(user_id)

        # لا يمكن حذف الإدارة الوحيدة
        admin_count = User.query.filter_by(role='admin').count()
        if user.is_admin() and admin_count <= 1:
            return jsonify({'success': False, 'message': 'لا يمكن حذف الإدارة الوحيدة'}), 400

        username = user.username
        db.session.delete(user)

        # تسجيل في سجل الأنشطة
        admin_user = User.query.get(session['user_id'])
        audit_log = AuditLog(
            user_id=admin_user.id,
            action=f"حذف المستخدم: {username}"
        )
        db.session.add(audit_log)
        db.session.commit()

        return jsonify({'success': True, 'message': 'تم حذف المستخدم بنجاح'})

    except Exception as e:
        logger.error(f"خطأ في حذف المستخدم: {str(e)}")
        return jsonify({'success': False, 'message': 'خطأ في الخادم'}), 500

# ==================== Network Management APIs ====================

@admin_bp.route('/networks/add', methods=['POST'])
@require_admin
def add_network():
    """إضافة شبكة جديدة"""
    try:
        data = request.get_json()

        # التحقق من البيانات المطلوبة
        required_fields = ['name', 'user_id']
        for field in required_fields:
            if not data.get(field):
                return jsonify({'success': False, 'message': f'حقل {field} مطلوب'}), 400

        # التحقق من وجود المستخدم
        user = User.query.get(data['user_id'])
        if not user:
            return jsonify({'success': False, 'message': 'المستخدم غير موجود'}), 404

        # إنشاء الشبكة
        network = Network(
            name=data['name'],
            mikrotik_ip=data.get('mikrotik_ip'),
            mikrotik_port=data.get('mikrotik_port', 8728),
            mikrotik_username=data.get('mikrotik_username'),
            mikrotik_password=data.get('mikrotik_password'),
            description=data.get('description'),
            user_id=data['user_id']
        )

        db.session.add(network)
        db.session.commit()

        # تسجيل في سجل الأنشطة
        admin_user = User.query.get(session['user_id'])
        audit_log = AuditLog(
            user_id=admin_user.id,
            action=f"إضافة شبكة جديدة: {network.name}",
            details=f"للمستخدم: {user.username}"
        )
        db.session.add(audit_log)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'تم إضافة الشبكة بنجاح',
            'network_id': network.id
        }), 201

    except Exception as e:
        logger.error(f"خطأ في إضافة الشبكة: {str(e)}")
        return jsonify({'success': False, 'message': 'خطأ في الخادم'}), 500

@admin_bp.route('/networks/<int:network_id>', methods=['PUT'])
@require_admin
def update_network(network_id):
    """تحديث بيانات الشبكة"""
    try:
        network = Network.query.get_or_404(network_id)
        data = request.get_json()

        # تحديث البيانات
        if 'name' in data:
            network.name = data['name']
        if 'mikrotik_ip' in data:
            network.mikrotik_ip = data['mikrotik_ip']
        if 'mikrotik_port' in data:
            network.mikrotik_port = data['mikrotik_port']
        if 'mikrotik_username' in data:
            network.mikrotik_username = data['mikrotik_username']
        if 'mikrotik_password' in data:
            network.mikrotik_password = data['mikrotik_password']
        if 'description' in data:
            network.description = data['description']
        if 'user_id' in data:
            network.user_id = data['user_id']
        if 'is_active' in data:
            network.is_active = data['is_active']

        network.updated_at = datetime.utcnow()
        db.session.commit()

        # تسجيل في سجل الأنشطة
        admin_user = User.query.get(session['user_id'])
        audit_log = AuditLog(
            user_id=admin_user.id,
            action=f"تحديث الشبكة: {network.name}",
            details=f"التغييرات: {', '.join(data.keys())}"
        )
        db.session.add(audit_log)
        db.session.commit()

        return jsonify({'success': True, 'message': 'تم تحديث الشبكة بنجاح'})

    except Exception as e:
        logger.error(f"خطأ في تحديث الشبكة: {str(e)}")
        return jsonify({'success': False, 'message': 'خطأ في الخادم'}), 500

@admin_bp.route('/networks/<int:network_id>', methods=['DELETE'])
@require_admin
def delete_network(network_id):
    """حذف الشبكة"""
    try:
        network = Network.query.get_or_404(network_id)
        network_name = network.name

        db.session.delete(network)

        # تسجيل في سجل الأنشطة
        admin_user = User.query.get(session['user_id'])
        audit_log = AuditLog(
            user_id=admin_user.id,
            action=f"حذف الشبكة: {network_name}"
        )
        db.session.add(audit_log)
        db.session.commit()

        return jsonify({'success': True, 'message': 'تم حذف الشبكة بنجاح'})

    except Exception as e:
        logger.error(f"خطأ في حذف الشبكة: {str(e)}")
        return jsonify({'success': False, 'message': 'خطأ في الخادم'}), 500

# ==================== Statistics APIs ====================

@admin_bp.route('/stats', methods=['GET'])
@require_admin
def get_admin_stats():
    """الحصول على إحصائيات الإدارة"""
    try:
        users_count = User.query.count()
        networks_count = Network.query.count()
        devices_count = Device.query.count()
        online_devices = Device.query.filter_by(status='online').count()
        warning_devices = Device.query.filter_by(status='warning').count()
        offline_devices = Device.query.filter_by(status='offline').count()

        # إحصائيات المستخدمين حسب الدور
        admin_count = User.query.filter_by(role='admin').count()
        distributor_count = User.query.filter_by(role='distributor').count()

        return jsonify({
            'success': True,
            'stats': {
                'users': {
                    'total': users_count,
                    'admins': admin_count,
                    'distributors': distributor_count
                },
                'networks': networks_count,
                'devices': {
                    'total': devices_count,
                    'online': online_devices,
                    'warning': warning_devices,
                    'offline': offline_devices
                }
            }
        })

    except Exception as e:
        logger.error(f"خطأ في جلب الإحصائيات: {str(e)}")
        return jsonify({'success': False, 'message': 'خطأ في الخادم'}), 500

# ==================== API Endpoints for Frontend ====================

@admin_bp.route('/api/users', methods=['GET'])
@require_admin
def api_get_users():
    """API للحصول على قائمة المستخدمين مع دعم التصفية والترتيب"""
    try:
        # معاملات الاستعلام
        limit = request.args.get('limit', type=int)
        sort = request.args.get('sort', 'created_at')
        order = request.args.get('order', 'desc')

        # التحقق من صحة معاملات الترتيب
        allowed_sort_fields = ['id', 'username', 'email', 'role', 'created_at', 'updated_at']
        if sort not in allowed_sort_fields:
            sort = 'created_at'

        if order not in ['asc', 'desc']:
            order = 'desc'

        # بناء الاستعلام
        query = User.query

        # تطبيق الترتيب
        if order == 'desc':
            query = query.order_by(getattr(User, sort).desc())
        else:
            query = query.order_by(getattr(User, sort).asc())

        # تطبيق الحد
        if limit:
            query = query.limit(limit)

        users = query.all()

        # تحويل إلى JSON
        users_data = []
        for user in users:
            users_data.append({
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'role': user.role,
                'full_name': user.full_name,
                'phone': user.phone,
                'company': user.company,
                'is_active': user.is_active,
                'created_at': user.created_at.isoformat() if user.created_at else None,
                'updated_at': user.updated_at.isoformat() if user.updated_at else None
            })

        return jsonify({
            'success': True,
            'users': users_data,
            'total': len(users_data)
        })

    except Exception as e:
        logger.error(f"خطأ في جلب المستخدمين: {str(e)}")
        return jsonify({'success': False, 'message': 'خطأ في الخادم'}), 500

@admin_bp.route('/api/networks', methods=['GET'])
@require_admin
def api_get_networks():
    """API للحصول على قائمة الشبكات مع دعم التصفية والترتيب"""
    try:
        # معاملات الاستعلام
        limit = request.args.get('limit', type=int)
        sort = request.args.get('sort', 'created_at')
        order = request.args.get('order', 'desc')

        # التحقق من صحة معاملات الترتيب
        allowed_sort_fields = ['id', 'name', 'created_at', 'updated_at']
        if sort not in allowed_sort_fields:
            sort = 'created_at'

        if order not in ['asc', 'desc']:
            order = 'desc'

        # بناء الاستعلام
        query = Network.query.join(User, Network.user_id == User.id)

        # تطبيق الترتيب
        if order == 'desc':
            query = query.order_by(getattr(Network, sort).desc())
        else:
            query = query.order_by(getattr(Network, sort).asc())

        # تطبيق الحد
        if limit:
            query = query.limit(limit)

        networks = query.all()

        # تحويل إلى JSON
        networks_data = []
        for network in networks:
            networks_data.append({
                'id': network.id,
                'name': network.name,
                'mikrotik_ip': network.mikrotik_ip,
                'mikrotik_port': network.mikrotik_port,
                'mikrotik_username': network.mikrotik_username,
                'mikrotik_password': network.mikrotik_password,
                'description': network.description,
                'is_active': network.is_active,
                'user_id': network.user_id,
                'owner_username': network.owner.username if network.owner else None,
                'device_count': network.get_device_count(),
                'created_at': network.created_at.isoformat() if network.created_at else None,
                'updated_at': network.updated_at.isoformat() if network.updated_at else None
            })

        return jsonify({
            'success': True,
            'networks': networks_data,
            'total': len(networks_data)
        })

    except Exception as e:
        logger.error(f"خطأ في جلب الشبكات: {str(e)}")
        return jsonify({'success': False, 'message': 'خطأ في الخادم'}), 500

# إضافة المسارات الإضافية
admin_bp.add_url_rule('/api/users', 'api_get_users', api_get_users, methods=['GET'])
admin_bp.add_url_rule('/api/networks', 'api_get_networks', api_get_networks, methods=['GET'])