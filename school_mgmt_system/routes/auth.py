from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from models.database import get_db

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/')
def index():
    if session.get('role') == 'admin':
        return redirect(url_for('admin.panel'))
    if session.get('role') == 'teacher':
        return redirect(url_for('main.dashboard'))
    return redirect(url_for('auth.login'))


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        role = request.form.get('role', 'teacher')

        conn = get_db()
        cursor = conn.cursor()

        if role == 'admin':
            cursor.execute("SELECT * FROM admin WHERE username = ?", (username,))
            user = cursor.fetchone()
            if user and check_password_hash(user['password'], password):
                session.clear()
                session['user_id'] = user['id']
                session['username'] = user['username']
                session['role'] = 'admin'
                conn.close()
                flash('Welcome Admin!', 'success')
                return redirect(url_for('admin.panel'))
        else:
            cursor.execute("SELECT * FROM teachers WHERE username = ? AND is_active = 1", (username,))
            user = cursor.fetchone()
            if user and check_password_hash(user['password'], password):
                session.clear()
                session['user_id'] = user['id']
                session['username'] = user['username']
                session['role'] = 'teacher'
                session['teacher_name'] = user['name']
                conn.close()
                if not user['is_setup_complete']:
                    return redirect(url_for('teacher.setup'))
                flash(f'Welcome {user["name"] or user["username"]}!', 'success')
                return redirect(url_for('main.dashboard'))

        conn.close()
        flash('Invalid credentials', 'danger')

    return render_template('login.html')


@auth_bp.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully', 'info')
    return redirect(url_for('auth.login'))
