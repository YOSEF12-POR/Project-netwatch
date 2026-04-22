from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from enum import Enum
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

# ============ Enums ============
class UserRole(Enum):
    ADMIN = "admin"
    DISTRIBUTOR = "distributor"

class DeviceStatus(Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    WARNING = "warning"
    UNKNOWN = "unknown"

# ============ Models ============

class User(db.Model):
    """جدول المستخدمين"""
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="distributor", nullable=True)  # admin or distributor
    full_name = db.Column(db.String(100), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    company = db.Column(db.String(100), nullable=True)
    network_id = db.Column(db.Integer, db.ForeignKey('networks.id'), nullable=True)  # الشبكة التي ينتمي إليها المستخدم
    email_verified = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # العلاقات
    networks = db.relationship('Network', backref='owner', lazy=True, cascade='all, delete-orphan', foreign_keys='Network.user_id')
    network = db.relationship('Network', backref='users', lazy=True, foreign_keys='User.network_id')

    def __repr__(self):
        return f'<User {self.username} ({self.role})>'

    def is_admin(self):
        return self.role == "admin"

    def is_distributor(self):
        return self.role == "distributor"

    def can_manage_network(self, network_id):
        """التحقق من صلاحية إدارة الشبكة"""
        if self.is_admin():
            return True
        return any(network.id == network_id for network in self.networks)

    def set_password(self, password):
        """تعيين كلمة المرور المشفرة"""
        self.password = generate_password_hash(password)

    def check_password(self, password):
        """التحقق من كلمة المرور"""
        return check_password_hash(self.password, password)

    def get_networks(self):
        """الحصول على الشبكات المسموحة للمستخدم"""
        if self.is_admin():
            return Network.query.all()
        if self.networks:
            return self.networks
        if self.network:
            return [self.network]
        return []


class Network(db.Model):
    """جدول الشبكات"""
    __tablename__ = 'networks'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    mikrotik_ip = db.Column(db.String(50), nullable=True)  # IP الراوتر
    mikrotik_port = db.Column(db.Integer, default=8728)  # Port SSH/API
    public_wan_ip = db.Column(db.String(64), nullable=True)  # IP العام العام (لربط واجهة WAN ومراقبة TX/RX)
    mikrotik_username = db.Column(db.String(50), nullable=True)
    mikrotik_password = db.Column(db.String(255), nullable=True)  # غير مستخدم للموزعين — الاعتماد على جلسة المستخدم
    mikrotik_onboarding_complete = db.Column(db.Boolean, default=False)  # إكمال إعداد الاتصال لأول مرة (بدون تخزين كلمة المرور)
    description = db.Column(db.Text, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)  # المالك
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # العلاقات
    devices = db.relationship('Device', backref='network', lazy=True, cascade='all, delete-orphan')
    scans = db.relationship('Scan', backref='network', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Network {self.name}>'

    def get_device_count(self):
        """عدد الأجهزة في الشبكة"""
        return len(self.devices)

    def get_online_devices(self):
        """عدد الأجهزة المتصلة"""
        return sum(1 for device in self.devices if device.status == 'online')

    def get_alerts_count(self):
        """عدد التنبيهات"""
        return sum(1 for device in self.devices if device.status == 'warning')


class Device(db.Model):
    """جدول الأجهزة"""
    __tablename__ = 'devices'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    ip_address = db.Column(db.String(50), nullable=False)
    mac_address = db.Column(db.String(17), unique=True, nullable=True)
    device_type = db.Column(db.String(50), default="Unknown")  # Router, Switch, PC, etc.
    description = db.Column(db.Text, nullable=True)
    network_id = db.Column(db.Integer, db.ForeignKey('networks.id'), nullable=False)
    status = db.Column(db.String(20), default="unknown")  # online/offline/warning/unknown
    last_ping = db.Column(db.Float, nullable=True)  # response time in ms
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # العلاقات
    status_logs = db.relationship('DeviceStatusLog', backref='device', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Device {self.name} ({self.ip_address}) - {self.status}>'

    def is_online(self):
        return self.status == 'online'

    def is_warning(self):
        return self.status == 'warning'

    def is_offline(self):
        return self.status == 'offline'


class Scan(db.Model):
    """جدول عمليات المسح"""
    __tablename__ = 'scans'

    id = db.Column(db.Integer, primary_key=True)
    network_id = db.Column(db.Integer, db.ForeignKey('networks.id'), nullable=False)
    interface = db.Column(db.String(100), nullable=False)  # ether1, ether2, ...
    ip_range = db.Column(db.String(100), nullable=False)  # 192.168.1.0/24
    status = db.Column(db.String(20), default="pending")  # pending, running, completed, failed
    devices_found = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)
    
    def __repr__(self):
        return f'<Scan {self.ip_range}>'


class DeviceStatusLog(db.Model):
    """جدول سجل حالة الأجهزة"""
    __tablename__ = 'device_status_logs'

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('devices.id'), nullable=False)
    status = db.Column(db.String(20), nullable=False)  # online/offline/warning/unknown
    previous_status = db.Column(db.String(20), nullable=True)
    response_time = db.Column(db.Float, nullable=True)  # ms
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<StatusLog {self.device_id} - {self.status}>'


class AuditLog(db.Model):
    """جدول سجل الأنشطة"""
    __tablename__ = 'audit_logs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    action = db.Column(db.String(200), nullable=False)
    ip_address = db.Column(db.String(50), nullable=True)
    details = db.Column(db.Text, nullable=True)  # JSON or detailed info
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<AuditLog {self.user_id} - {self.action}>'