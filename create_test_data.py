#!/usr/bin/env python3
"""
إنشاء بيانات تجريبية للموزعين والمستخدمين
"""
from dotenv import load_dotenv
load_dotenv()

try:
    from app import create_app
    from app.models.models import db, User, Network
    from sqlalchemy import text

    app = create_app()
    with app.app_context():
        print("بدء إنشاء البيانات...")

        # حذف البيانات القديمة (بالترتيب الصحيح)
        try:
            # أولاً نضع network_id = None للمستخدمين
            db.session.execute(text("UPDATE users SET network_id = NULL WHERE network_id IS NOT NULL"))
            db.session.commit()
            print("تم تحديث المستخدمين")
            
            # ثم نحذف الشبكات
            db.session.query(Network).delete()
            db.session.commit()
            print("تم حذف الشبكات")
            
            # ثم نحذف المستخدمين
            db.session.query(User).delete()
            db.session.commit()
            print("تم حذف المستخدمين")
        except Exception as e:
            print(f"خطأ في الحذف: {e}")
            # إذا فشل، نحاول إعادة إنشاء قاعدة البيانات
            db.create_all()
            print("تم إعادة إنشاء قاعدة البيانات")

        # إنشاء مستخدم موزع
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
        print("تم إنشاء الموزع")

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
        print("تم إنشاء الشبكة")

        # إنشاء مستخدمين للشبكة
        user1 = User(
            username='user1',
            email='user1@test.com',
            full_name='مستخدم تجريبي',
            role='user',
            network_id=network.id,
            is_active=True
        )
        user1.set_password('password123')
        db.session.add(user1)

        user2 = User(
            username='user2',
            email='user2@test.com',
            full_name='مستخدم تجريبي 2',
            role='user',
            network_id=network.id,
            is_active=True
        )
        user2.set_password('password123')
        db.session.add(user2)

        db.session.commit()
        print("تم إنشاء المستخدمين")

        print('تم إنشاء البيانات التجريبية بنجاح!')
        print('الموزع: distributor / 123456')
        print('المستخدمون: user1, user2 / password123')

except Exception as e:
    print(f"خطأ: {e}")
    import traceback
    traceback.print_exc()