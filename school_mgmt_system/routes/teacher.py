from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from models.database import get_db
from utils.auth import login_required

teacher_bp = Blueprint('teacher', __name__)


@teacher_bp.route('/setup', methods=['GET', 'POST'])
@login_required(role='teacher')
def setup():
    teacher_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM teachers WHERE id = ?", (teacher_id,))
    teacher = cursor.fetchone()
    cursor.execute("SELECT * FROM subjects WHERE teacher_id = ? ORDER BY name", (teacher_id,))
    subjects = cursor.fetchall()
    cursor.execute("SELECT * FROM students WHERE teacher_id = ? ORDER BY roll_no", (teacher_id,))
    students = cursor.fetchall()

    if request.method == 'POST':
        name = request.form['name'].strip()
        school_name = request.form['school_name'].strip()
        mobile = request.form['mobile'].strip()
        class_name = request.form['class_name'].strip()
        division = request.form['division'].strip()
        academic_year = request.form['academic_year'].strip()
        subjects = [subject.strip() for subject in request.form.getlist('subjects') if subject.strip()]

        cursor.execute(
            '''
            UPDATE teachers
            SET name = ?, school_name = ?, mobile = ?, class_name = ?, division = ?, academic_year = ?, is_setup_complete = 1
            WHERE id = ?
            ''',
            (name, school_name, mobile, class_name, division, academic_year, teacher_id),
        )

        cursor.execute("DELETE FROM subjects WHERE teacher_id = ?", (teacher_id,))
        for subject in dict.fromkeys(subjects):
            cursor.execute(
                "INSERT INTO subjects (teacher_id, name) VALUES (?, ?)",
                (teacher_id, subject),
            )

        roll_numbers = request.form.getlist('student_roll_no')
        student_names = request.form.getlist('student_name')
        father_names = request.form.getlist('father_name')
        mother_names = request.form.getlist('mother_name')
        phone_numbers = request.form.getlist('student_phone')

        for roll_no, student_name, father_name, mother_name, phone in zip(
            roll_numbers,
            student_names,
            father_names,
            mother_names,
            phone_numbers,
        ):
            if not roll_no.strip() or not student_name.strip():
                continue
            cursor.execute(
                '''
                INSERT INTO students (teacher_id, roll_no, name, father_name, mother_name, phone)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(teacher_id, roll_no) DO UPDATE SET
                    name = excluded.name,
                    father_name = excluded.father_name,
                    mother_name = excluded.mother_name,
                    phone = excluded.phone
                ''',
                (
                    teacher_id,
                    int(roll_no),
                    student_name.strip(),
                    father_name.strip(),
                    mother_name.strip(),
                    phone.strip(),
                ),
            )

        conn.commit()
        conn.close()

        session['teacher_name'] = name
        flash('Setup completed successfully.', 'success')
        return redirect(url_for('main.dashboard'))

    conn.close()
    return render_template('teacher/setup.html', teacher=teacher, subjects=subjects, students=students)
