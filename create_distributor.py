from app import create_app
from app.models.models import db, User, Network

app = create_app()
with app.app_context():
    # التحقق من وجود المستخدم أولاً
    existing_distributor = User.query.filter_by(username='distributor').first()
    if existing_distributor:
        print('مستخدم distributor موجود بالفعل')
        print('اسم المستخدم: distributor')
        print('كلمة المرور: 123456')
        print('البريد الإلكتروني: distributor@test.com')
    else:
        # إنشاء مستخدم موزع أولاً
        distributor = User(
            username='distributor',
            email='distributor@test.com',
            full_name='موزع تجريبي',
            role='distributor',
            is_active=True
        )
        distributor.set_password('123456')
        db.session.add(distributor)
        db.session.commit()

        # إنشاء شبكة للموزع
        network = Network(
            name='شبكة الموزع التجريبية',
            mikrotik_ip='192.168.1.1',
            mikrotik_port=8728,
            mikrotik_username=None,
            mikrotik_password=None,
            mikrotik_onboarding_complete=False,
            user_id=distributor.id,
            is_active=True
        )
        db.session.add(network)
        db.session.commit()

        print('تم إنشاء مستخدم موزع:')
        print('اسم المستخدم: distributor')
        print('كلمة المرور: 123456')
        print('البريد الإلكتروني: distributor@test.com')