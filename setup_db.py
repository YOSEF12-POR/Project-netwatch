from app import create_app
from app.models.models import db, User
from werkzeug.security import generate_password_hash

app = create_app()

with app.app_context():
    try:
        # Create tables
        db.create_all()
        print('Tables created successfully')

        # Create admin user
        admin = User.query.filter_by(username='admin').first()
        if not admin:
            admin = User(
                username='admin',
                email='admin@netwatch.com',
                password=generate_password_hash('admin123'),
                role='admin',
                full_name='Admin',
                is_active=True
            )
            db.session.add(admin)
            db.session.commit()
            print('Admin user created successfully')
            print('Username: admin')
            print('Password: admin123')
        else:
            print('Admin user already exists')

    except Exception as e:
        print(f'Error: {e}')
        db.session.rollback()