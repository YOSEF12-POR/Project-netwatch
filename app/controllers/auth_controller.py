from flask import Blueprint, render_template, request, redirect, session, flash, url_for
from app.models.models import db, User
from app.utils.connection_manager import ConnectionManager
from app.services.auth_service import AuthService
from werkzeug.security import generate_password_hash, check_password_hash
from app.utils.security import rate_limit

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/register', methods=['GET', 'POST'])
@rate_limit("public")
def register():
    """صفحة التسجيل الجديد"""
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        
        # استدعاء خدمة التسجيل
        success, message = AuthService.register_user(username, email, password, confirm_password)
        
        if success:
            flash(message, 'success')
            return redirect(url_for('auth.login'))
        else:
            flash(message, 'danger')
    
    return render_template('register.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
@rate_limit("public")
def login():
    """صفحة تسجيل الدخول"""
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        # التحقق من صحة البيانات
        if not username or not password:
            flash('الرجاء إدخال اسم المستخدم وكلمة المرور', 'danger')
            return render_template('login.html')
        
        # استدعاء خدمة تسجيل الدخول
        success, user, message = AuthService.login_user(username, password)
        
        if success and user:
            session['user_id'] = user.id
            session['username'] = user.username
            flash(message, 'success')
            
            # توجيه المستخدم حسب الدور
            if user.is_admin():
                return redirect(url_for('admin.admin_dashboard'))
            else:
                return redirect(url_for('dashboard.dashboard'))
        else:
            flash(message, 'danger')
    
    return render_template('login.html')


@auth_bp.route('/dashboard')
def dashboard():
    """صفحة لوحة التحكم"""
    if 'user_id' not in session:
        flash('الرجاء تسجيل الدخول أولاً', 'warning')
        return redirect(url_for('auth.login'))

    # استخدم لوحة التحكم الأساسية (تقوم بتحميل الشبكات/الأجهزة)
    return redirect(url_for('dashboard.dashboard'))


@auth_bp.route('/logout')
def logout():
    """تسجيل الخروج — قطع اتصالات MikroTik المؤقتة وحذف بيانات الجلسة."""
    user_id = session.get('user_id')
    if user_id:
        user = User.query.get(user_id)
        if user:
            try:
                nets = user.get_networks()
                ConnectionManager.close_connections_for_network_ids([n.id for n in nets])
            except Exception:
                pass
    session.pop('mikrotik_creds', None)
    session.clear()
    flash('تم تسجيل الخروج بنجاح', 'info')
    return redirect(url_for('auth.login'))