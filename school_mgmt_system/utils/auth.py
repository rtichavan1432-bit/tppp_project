from functools import wraps

from flask import flash, redirect, session, url_for


def login_required(role=None):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                flash('Please login first', 'warning')
                return redirect(url_for('auth.login'))
            if role and session.get('role') != role:
                flash('Unauthorized access', 'danger')
                if session.get('role') == 'admin':
                    return redirect(url_for('admin.panel'))
                if session.get('role') == 'teacher':
                    return redirect(url_for('main.dashboard'))
                return redirect(url_for('auth.login'))
            return f(*args, **kwargs)

        return decorated_function

    return decorator


def calculate_grade(percentage):
    if percentage >= 90:
        return 'A+', 'Outstanding'
    if percentage >= 80:
        return 'A', 'Excellent'
    if percentage >= 70:
        return 'B', 'Very Good'
    if percentage >= 60:
        return 'C', 'Good'
    if percentage >= 40:
        return 'D', 'Needs Improvement'
    return 'F', 'Fail'


def calculate_rank(marks_list, student_id):
    sorted_marks = sorted(marks_list, key=lambda x: x['marks_obtained'], reverse=True)
    for idx, mark in enumerate(sorted_marks, 1):
        if mark['student_id'] == student_id:
            return idx
    return len(sorted_marks)
