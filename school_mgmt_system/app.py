# app.py
from flask import Flask
from models.database import init_db
from routes.auth import auth_bp
from routes.admin import admin_bp
from routes.teacher import teacher_bp
from routes.main import main_bp
import os

app = Flask(__name__)
app.secret_key = 'school-management-secret-key-2024'
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False

for folder in (
    app.config['UPLOAD_FOLDER'],
    os.path.join(app.config['UPLOAD_FOLDER'], 'notes'),
    os.path.join(app.config['UPLOAD_FOLDER'], 'papers'),
):
    os.makedirs(folder, exist_ok=True)

# Register blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(teacher_bp)
app.register_blueprint(main_bp)

# Initialize database on first run
with app.app_context():
    init_db()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
