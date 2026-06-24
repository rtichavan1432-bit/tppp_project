import sqlite3

from flask import Blueprint, flash, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from models.database import get_db
from utils.auth import login_required

admin_bp = Blueprint('admin', __name__)


def _delete_teacher_records(cursor, teacher_id):
    cursor.execute("SELECT id FROM fees WHERE teacher_id = ?", (teacher_id,))
    fee_ids = [row['id'] for row in cursor.fetchall()]
    for fee_id in fee_ids:
        cursor.execute("DELETE FROM fee_transactions WHERE fee_id = ?", (fee_id,))

    cursor.execute("SELECT id FROM exams WHERE teacher_id = ?", (teacher_id,))
    exam_ids = [row['id'] for row in cursor.fetchall()]
    for exam_id in exam_ids:
        cursor.execute("DELETE FROM marks WHERE exam_id = ?", (exam_id,))

    cursor.execute("DELETE FROM attendance WHERE teacher_id = ?", (teacher_id,))
    cursor.execute("DELETE FROM notes WHERE teacher_id = ?", (teacher_id,))
    cursor.execute("DELETE FROM question_papers WHERE teacher_id = ?", (teacher_id,))
    cursor.execute("DELETE FROM fees WHERE teacher_id = ?", (teacher_id,))
    cursor.execute("DELETE FROM exams WHERE teacher_id = ?", (teacher_id,))
    cursor.execute("DELETE FROM subjects WHERE teacher_id = ?", (teacher_id,))
    cursor.execute("DELETE FROM students WHERE teacher_id = ?", (teacher_id,))
    cursor.execute("DELETE FROM users WHERE teacher_id = ?", (teacher_id,))
    cursor.execute("DELETE FROM teachers WHERE id = ?", (teacher_id,))


@admin_bp.route('/admin')
@login_required(role='admin')
def panel():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) AS total FROM teachers")
    total_teachers = cursor.fetchone()['total']

    cursor.execute("SELECT COUNT(*) AS active FROM teachers WHERE is_active = 1")
    active_teachers = cursor.fetchone()['active']

    cursor.execute("SELECT COUNT(*) AS disabled FROM teachers WHERE is_active = 0")
    disabled_teachers = cursor.fetchone()['disabled']

    cursor.execute(
        '''
        SELECT
            t.*,
            COUNT(DISTINCT s.id) AS student_count,
            COUNT(DISTINCT sub.id) AS subject_count,
            COUNT(DISTINCT e.id) AS exam_count,
            COALESCE(SUM(CASE WHEN f.annual_fee > f.paid_amount THEN f.annual_fee - f.paid_amount ELSE 0 END), 0) AS pending_fees
        FROM teachers t
        LEFT JOIN students s ON s.teacher_id = t.id
        LEFT JOIN subjects sub ON sub.teacher_id = t.id
        LEFT JOIN exams e ON e.teacher_id = t.id
        LEFT JOIN fees f ON f.teacher_id = t.id
        GROUP BY t.id
        ORDER BY t.created_at DESC
        '''
    )
    teachers = cursor.fetchall()

    conn.close()
    return render_template(
        'admin/panel.html',
        total_teachers=total_teachers,
        active_teachers=active_teachers,
        disabled_teachers=disabled_teachers,
        teachers=teachers,
    )


@admin_bp.route('/admin/teacher/create', methods=['POST'])
@login_required(role='admin')
def create_teacher():
    username = request.form['username'].strip()
    password = request.form['password']
    name = request.form['name'].strip()

    conn = get_db()
    cursor = conn.cursor()

    try:
        password_hash = generate_password_hash(password)
        cursor.execute(
            '''
            INSERT INTO teachers (username, password, name)
            VALUES (?, ?, ?)
            ''',
            (username, password_hash, name),
        )
        teacher_id = cursor.lastrowid
        cursor.execute(
            '''
            INSERT INTO users (username, password, role, teacher_id, is_active)
            VALUES (?, ?, 'teacher', ?, 1)
            ''',
            (username, password_hash, teacher_id),
        )
        conn.commit()
        flash('Teacher created successfully.', 'success')
    except sqlite3.IntegrityError:
        flash('Username already exists.', 'danger')

    conn.close()
    return redirect(url_for('admin.panel'))


@admin_bp.route('/admin/teacher/reset-password/<int:teacher_id>', methods=['POST'])
@login_required(role='admin')
def reset_teacher_password(teacher_id):
    new_password = request.form['new_password'].strip()
    if len(new_password) < 6:
        flash('Password must be at least 6 characters.', 'danger')
        return redirect(url_for('admin.panel'))

    conn = get_db()
    cursor = conn.cursor()
    password_hash = generate_password_hash(new_password)
    cursor.execute("UPDATE teachers SET password = ? WHERE id = ?", (password_hash, teacher_id))
    cursor.execute("UPDATE users SET password = ? WHERE teacher_id = ?", (password_hash, teacher_id))
    conn.commit()
    conn.close()

    flash('Teacher password reset successfully.', 'success')
    return redirect(url_for('admin.panel'))


@admin_bp.route('/admin/teacher/toggle/<int:teacher_id>', methods=['POST'])
@login_required(role='admin')
def toggle_teacher(teacher_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE teachers SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END WHERE id = ?",
        (teacher_id,),
    )
    cursor.execute(
        "UPDATE users SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END WHERE teacher_id = ?",
        (teacher_id,),
    )
    conn.commit()
    conn.close()

    flash('Teacher status updated.', 'success')
    return redirect(url_for('admin.panel'))


@admin_bp.route('/admin/teacher/delete/<int:teacher_id>', methods=['POST'])
@login_required(role='admin')
def delete_teacher(teacher_id):
    conn = get_db()
    cursor = conn.cursor()
    _delete_teacher_records(cursor, teacher_id)
    conn.commit()
    conn.close()

    flash('Teacher deleted successfully.', 'success')
    return redirect(url_for('admin.panel'))
