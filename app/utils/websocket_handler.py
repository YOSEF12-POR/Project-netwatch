"""
WebSocket Handler للتحديثات الحية
يوفر تحديثات شاملة لحالة الشبكة والأجهزة
"""
import json
import asyncio
from datetime import datetime
from flask import current_app
from flask_socketio import emit, join_room, leave_room
from app.models.models import Device, DeviceStatusLog, AuditLog
from app.services.network_service import NetworkService
from app.services.device_service import DeviceService

class DashboardUpdater:
    """مدير تحديثات لوحة التحكم"""
    
    def __init__(self, socketio):
        self.socketio = socketio
        self.active_rooms = {}
        
    async def get_dashboard_status(self, network_id=None):
        """الحصول على حالة لوحة التحكم الحالية"""
        try:
            devices = Device.query.all()
            online_count = sum(1 for d in devices if d.status == 'online')
            alerts_count = DeviceStatusLog.query.filter_by(status='warning').count()
            
            return {
                'timestamp': datetime.utcnow().isoformat(),
                'online_count': online_count,
                'total_count': len(devices),
                'alerts_count': alerts_count,
                'network_status': 'healthy' if alerts_count == 0 else 'warning',
                'devices': [
                    {
                        'id': d.id,
                        'name': d.name,
                        'ip': d.ip_address,
                        'mac': d.mac_address,
                        'type': d.device_type,
                        'status': d.status,
                        'ping': getattr(d, 'last_ping', 0),
                        'last_check': d.last_updated.isoformat() if d.last_updated else None
                    }
                    for d in devices
                ]
            }
        except Exception as e:
            current_app.logger.error(f"خطأ في الحصول على حالة Dashboard: {str(e)}")
            return {
                'error': str(e),
                'online_count': 0,
                'total_count': 0,
                'alerts_count': 0
            }
    
    async def broadcast_status_update(self, namespace='/ws/dashboard'):
        """بث تحديث الحالة لجميع المتصلين"""
        try:
            status = await self.get_dashboard_status()
            self.socketio.emit('status_update', status, namespace=namespace)
        except Exception as e:
            current_app.logger.error(f"خطأ البث: {str(e)}")
    
    async def on_connect(self, data):
        """عند اتصال عميل جديد"""
        join_room('dashboard')
        status = await self.get_dashboard_status()
        emit('initial_state', status)
        emit('notification', {
            'type': 'info',
            'message': 'متصل بلوحة التحكم',
            'timestamp': datetime.utcnow().isoformat()
        })
    
    async def on_device_status_change(self, device_id, new_status, details=None):
        """عند تغيير حالة جهاز"""
        try:
            device = Device.query.get(device_id)
            if device:
                old_status = device.status
                device.status = new_status
                device.last_updated = datetime.utcnow()
                
                # تسجيل التغيير
                log = DeviceStatusLog(
                    device_id=device_id,
                    status=new_status,
                    previous_status=old_status,
                    details=json.dumps(details or {})
                )
                
                from app import db
                db.session.add(log)
                db.session.commit()
                
                # بث التحديث
                self.socketio.emit('device_status_changed', {
                    'device_id': device_id,
                    'device_name': device.name,
                    'old_status': old_status,
                    'new_status': new_status,
                    'timestamp': datetime.utcnow().isoformat(),
                    'details': details
                }, room='dashboard')
                
                # تنبيه
                icon = '🟢' if new_status == 'online' else '🔴' if new_status == 'offline' else '🟡'
                self.socketio.emit('notification', {
                    'type': 'status_change',
                    'title': f'{icon} حالة جهاز تغيرت',
                    'message': f'{device.name}: {old_status} → {new_status}',
                    'timestamp': datetime.utcnow().isoformat()
                }, room='dashboard')
        except Exception as e:
            current_app.logger.error(f"خطأ في تحديث حالة الجهاز: {str(e)}")


def create_socketio_handlers(socketio, app):
    """إنشء معالجات WebSocket"""
    
    updater = DashboardUpdater(socketio)
    
    @socketio.on('connect', namespace='/ws/dashboard')
    def handle_connect():
        """معالج الاتصال"""
        try:
            status = asyncio.run(updater.get_dashboard_status())
            emit('initial_state', status)
            print(f"✓ عميل متصل: {asyncio.current_task()}")
        except Exception as e:
            print(f"✗ خطأ الاتصال: {str(e)}")
    
    @socketio.on('disconnect', namespace='/ws/dashboard')
    def handle_disconnect():
        """معالج قطع الاتصال"""
        leave_room('dashboard')
        print("✗ عميل قطع الاتصال")
    
    @socketio.on('request_update', namespace='/ws/dashboard')
    def handle_update_request():
        """طلب تحديث يدوي"""
        try:
            status = asyncio.run(updater.get_dashboard_status())
            emit('status_update', status)
        except Exception as e:
            emit('error', {'message': str(e)})
    
    @socketio.on('ping', namespace='/ws/dashboard')
    def handle_ping(data):
        """معالج الـ ping للتحقق من الاتصال"""
        emit('pong', {'timestamp': datetime.utcnow().isoformat()})
    
    return updater
