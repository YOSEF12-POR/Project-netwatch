from app.models.models import db, User
from werkzeug.security import generate_password_hash, check_password_hash
import re
from sqlalchemy.exc import IntegrityError

class AuthService:
    """خدمة المصادقة والتحقق من بيانات المستخدم"""
    
    @staticmethod
    def validate_email(email):
        """التحقق من صحة البريد الإلكتروني"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return re.match(pattern, email) is not None
    
    @staticmethod
    def validate_username(username):
        """التحقق من اسم المستخدم"""
        if len(username) < 3:
            return False, "اسم المستخدم يجب أن يكون 3 أحرف على الأقل"
        if len(username) > 50:
            return False, "اسم المستخدم طويل جداً"
        if not re.match(r'^[a-zA-Z0-9_]+$', username):
            return False, "اسم المستخدم يجب أن يحتوي على أحرف وأرقام وشرطات سفلية فقط"
        return True, "صحيح"
    
    @staticmethod
    def validate_password(password):
        """التحقق من قوة كلمة المرور"""
        if len(password) < 8:
            return False, "كلمة المرور يجب أن تكون 8 أحرف على الأقل"
        if not re.search(r'[A-Z]', password):
            return False, "كلمة المرور يجب أن تحتوي على حرف كبير واحد على الأقل"
        if not re.search(r'[a-z]', password):
            return False, "كلمة المرور يجب أن تحتوي على حرف صغير واحد على الأقل"
        if not re.search(r'[0-9]', password):
            return False, "كلمة المرور يجب أن تحتوي على رقم واحد على الأقل"
        return True, "صحيح"
    
    @staticmethod
    def register_user(username, email, password, confirm_password, role='distributor',
                     full_name=None, phone=None, company=None):
        """تسجيل مستخدم جديد"""
        try:
            # التحقق من تطابق كلمات المرور
            if password != confirm_password:
                return False, "كلمات المرور غير متطابقة"

            # التحقق من صحة البريد الإلكتروني
            if not AuthService.validate_email(email):
                return False, "البريد الإلكتروني غير صحيح"
            
            # التحقق من اسم المستخدم
            is_valid, message = AuthService.validate_username(username)
            if not is_valid:
                return False, message
            
            # التحقق من قوة كلمة المرور
            is_valid, message = AuthService.validate_password(password)
            if not is_valid:
                return False, message
            
            # التحقق من عدم وجود مستخدم بنفس البريد أو الاسم
            existing_user = User.query.filter(
                (User.username == username) | (User.email == email)
            ).first()
            
            if existing_user:
                if existing_user.username == username:
                    return False, "اسم المستخدم موجود بالفعل"
                else:
                    return False, "البريد الإلكتروني موجود بالفعل"
            
            # إنشاء المستخدم الجديد
            hashed_password = generate_password_hash(password)
            new_user = User(
                username=username,
                email=email,
                password=hashed_password,
                role=role,  # الدور المحدد
                full_name=full_name,
                phone=phone,
                company=company,
                email_verified=True  # في الإنتاج: False ويتطلب تحقق بالبريد
            )
            
            db.session.add(new_user)
            db.session.commit()
            
            return True, f"تم التسجيل بنجاح! مرحباً بك {username}"
        
        except IntegrityError:
            db.session.rollback()
            return False, "حدث خطأ في الكاعدة البيانات"
        except Exception as e:
            db.session.rollback()
            return False, f"خطأ: {str(e)}"
    
    @staticmethod
    def login_user(username, password):
        """التحقق من بيانات تسجيل الدخول"""
        try:
            # البحث عن المستخدم باسم المستخدم أو البريد الإلكتروني
            user = User.query.filter(
                (User.username == username) | (User.email == username)
            ).first()
            
            if not user:
                return False, None, "اسم المستخدم أو كلمة المرور غير صحيحة"
            
            if not user.is_active:
                return False, None, "حسابك معطل مؤقتاً"
            
            if not check_password_hash(user.password, password):
                return False, None, "اسم المستخدم أو كلمة المرور غير صحيحة"
            
            return True, user, "تم تسجيل الدخول بنجاح"
        
        except Exception as e:
            return False, None, f"خطأ: {str(e)}"
    
    @staticmethod
    def get_user_by_id(user_id):
        """الحصول على بيانات المستخدم من معرفه"""
        try:
            return User.query.get(user_id)
        except Exception:
            return None
    
    @staticmethod
    def user_exists(identifier):
        """التحقق من وجود مستخدم"""
        return User.query.filter(
            (User.username == identifier) | (User.email == identifier)
        ).first() is not None
    
    @staticmethod
    def is_admin(user_id):
        """التحقق من كون المستخدم admin"""
        user = AuthService.get_user_by_id(user_id)
        return user and user.is_admin() if user else False
