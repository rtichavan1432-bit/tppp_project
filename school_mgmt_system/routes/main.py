import sqlite3
from datetime import date, datetime
from pathlib import Path

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

from models.database import get_db
from utils.auth import calculate_grade, calculate_rank, login_required
from utils.excel_export import (
    export_attendance,
    export_fees,
    export_marks,
    export_student_report,
    import_students_from_excel,
)

main_bp = Blueprint('main', __name__)


def _save_upload(file, subfolder):
    filename = secure_filename(file.filename or '')
    if not filename:
        raise ValueError("Invalid filename")

    unique_name = f"{datetime.now().timestamp()}_{filename}"
    upload_dir = Path(current_app.root_path) / 'static' / 'uploads' / subfolder
    upload_dir.mkdir(parents=True, exist_ok=True)

    file.save(upload_dir / unique_name)
    return f"uploads/{subfolder}/{unique_name}"


def _delete_student_records(cursor, teacher_id, student_id):
    cursor.execute("SELECT id FROM fees WHERE teacher_id = ? AND student_id = ?", (teacher_id, student_id))
    fee_ids = [row['id'] for row in cursor.fetchall()]
    for fee_id in fee_ids:
        cursor.execute("DELETE FROM fee_transactions WHERE fee_id = ?", (fee_id,))

    cursor.execute("DELETE FROM attendance WHERE teacher_id = ? AND student_id = ?", (teacher_id, student_id))
    cursor.execute("DELETE FROM marks WHERE student_id = ?", (student_id,))
    cursor.execute("DELETE FROM fees WHERE teacher_id = ? AND student_id = ?", (teacher_id, student_id))
    cursor.execute("DELETE FROM students WHERE teacher_id = ? AND id = ?", (teacher_id, student_id))


def _get_teacher_subjects(cursor, teacher_id):
    cursor.execute("SELECT * FROM subjects WHERE teacher_id = ? ORDER BY name", (teacher_id,))
    return cursor.fetchall()


def _get_teacher_students(cursor, teacher_id):
    cursor.execute(
        '''
        SELECT *
        FROM students
        WHERE teacher_id = ?
        ORDER BY roll_no
        ''',
        (teacher_id,),
    )
    return cursor.fetchall()


def _build_mark_matrix(cursor, teacher_id, exam_id):
    subjects = _get_teacher_subjects(cursor, teacher_id)
    students = _get_teacher_students(cursor, teacher_id)
    cursor.execute(
        '''
        SELECT student_id, subject_id, marks_obtained
        FROM marks
        WHERE exam_id = ?
        ''',
        (exam_id,),
    )
    marks = {(row['student_id'], row['subject_id']): row['marks_obtained'] for row in cursor.fetchall()}
    return subjects, students, marks


def _build_exam_results(cursor, teacher_id, exam_id):
    cursor.execute("SELECT * FROM exams WHERE id = ? AND teacher_id = ?", (exam_id, teacher_id))
    exam = cursor.fetchone()
    if not exam:
        return None, []

    subjects = _get_teacher_subjects(cursor, teacher_id)
    students = _get_teacher_students(cursor, teacher_id)
    subject_count = len(subjects)

    if subject_count == 0:
        return exam, []

    cursor.execute(
        '''
        SELECT student_id, subject_id, marks_obtained
        FROM marks
        WHERE exam_id = ?
        ''',
        (exam_id,),
    )
    marks_map = {(row['student_id'], row['subject_id']): row['marks_obtained'] for row in cursor.fetchall()}

    results = []
    for student in students:
        subject_marks = []
        total = 0
        for subject in subjects:
            mark = marks_map.get((student['id'], subject['id']))
            subject_marks.append({'subject_id': subject['id'], 'subject_name': subject['name'], 'marks': mark})
            total += float(mark or 0)

        total_possible = float(exam['max_marks']) * subject_count
        percentage = round((total / total_possible) * 100, 2) if total_possible else 0
        grade, remark = calculate_grade(percentage)
        results.append(
            {
                'student_id': student['id'],
                'roll_no': student['roll_no'],
                'name': student['name'],
                'subject_marks': subject_marks,
                'total': round(total, 2),
                'percentage': percentage,
                'grade': grade,
                'remark': remark,
            }
        )

    results.sort(key=lambda row: row['total'], reverse=True)
    for rank, row in enumerate(results, 1):
        row['rank'] = rank

    return exam, results


def _build_student_history(cursor, teacher_id, student_id):
    cursor.execute("SELECT * FROM students WHERE id = ? AND teacher_id = ?", (student_id, teacher_id))
    student = cursor.fetchone()
    if not student:
        return None, []

    subjects = _get_teacher_subjects(cursor, teacher_id)
    cursor.execute(
        '''
        SELECT *
        FROM exams
        WHERE teacher_id = ?
        ORDER BY COALESCE(exam_date, created_at) DESC, created_at DESC
        ''',
        (teacher_id,),
    )
    exams = cursor.fetchall()

    cursor.execute(
        '''
        SELECT exam_id, subject_id, marks_obtained
        FROM marks
        WHERE student_id = ?
        ''',
        (student_id,),
    )
    marks_map = {}
    for row in cursor.fetchall():
        marks_map.setdefault(row['exam_id'], {})[row['subject_id']] = row['marks_obtained']

    history = []
    for exam in exams:
        subject_marks = []
        total = 0
        for subject in subjects:
            mark = marks_map.get(exam['id'], {}).get(subject['id'])
            subject_marks.append({'subject_id': subject['id'], 'subject_name': subject['name'], 'marks': mark})
            total += float(mark or 0)

        subject_count = len(subjects)
        total_possible = float(exam['max_marks']) * subject_count
        percentage = round((total / total_possible) * 100, 2) if total_possible else 0
        grade, remark = calculate_grade(percentage)
        history.append(
            {
                'exam_id': exam['id'],
                'exam_name': exam['name'],
                'exam_date': exam['exam_date'],
                'subject_marks': subject_marks,
                'total': round(total, 2),
                'percentage': percentage,
                'grade': grade,
                'remark': remark,
                'max_marks_per_subject': exam['max_marks'],
                'total_possible': total_possible,
            }
        )

    return student, history


@main_bp.route('/dashboard')
@login_required(role='teacher')
def dashboard():
    teacher_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) AS total FROM students WHERE teacher_id = ?", (teacher_id,))
    total_students = cursor.fetchone()['total']

    cursor.execute("SELECT COUNT(*) AS total FROM subjects WHERE teacher_id = ?", (teacher_id,))
    total_subjects = cursor.fetchone()['total']

    today = date.today().isoformat()
    cursor.execute(
        '''
        SELECT COUNT(*) AS total
        FROM attendance
        WHERE teacher_id = ? AND date = ? AND status = 'present'
        ''',
        (teacher_id, today),
    )
    attendance_today = cursor.fetchone()['total']

    cursor.execute(
        '''
        SELECT COALESCE(SUM(CASE WHEN annual_fee > paid_amount THEN annual_fee - paid_amount ELSE 0 END), 0) AS pending
        FROM fees
        WHERE teacher_id = ?
        ''',
        (teacher_id,),
    )
    fees_pending = cursor.fetchone()['pending']

    cursor.execute(
        '''
        SELECT COUNT(*) AS total
        FROM exams
        WHERE teacher_id = ? AND exam_date IS NOT NULL AND exam_date >= ?
        ''',
        (teacher_id, today),
    )
    upcoming_exams = cursor.fetchone()['total']

    cursor.execute(
        '''
        SELECT roll_no, name
        FROM students
        WHERE teacher_id = ?
        ORDER BY created_at DESC
        LIMIT 5
        ''',
        (teacher_id,),
    )
    recent_students = cursor.fetchall()

    conn.close()
    return render_template(
        'teacher/dashboard.html',
        total_students=total_students,
        total_subjects=total_subjects,
        attendance_today=attendance_today,
        fees_pending=fees_pending,
        upcoming_exams=upcoming_exams,
        recent_students=recent_students,
    )


@main_bp.route('/profile', methods=['GET', 'POST'])
@login_required(role='teacher')
def profile():
    teacher_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor()

    if request.method == 'POST':
        cursor.execute(
            '''
            UPDATE teachers
            SET name = ?, school_name = ?, mobile = ?, class_name = ?, division = ?, academic_year = ?
            WHERE id = ?
            ''',
            (
                request.form['name'].strip(),
                request.form['school_name'].strip(),
                request.form['mobile'].strip(),
                request.form['class_name'].strip(),
                request.form['division'].strip(),
                request.form['academic_year'].strip(),
                teacher_id,
            ),
        )
        conn.commit()
        session['teacher_name'] = request.form['name'].strip()
        flash('Profile updated successfully.', 'success')

    cursor.execute("SELECT * FROM teachers WHERE id = ?", (teacher_id,))
    teacher = cursor.fetchone()

    cursor.execute("SELECT * FROM subjects WHERE teacher_id = ? ORDER BY name", (teacher_id,))
    subjects = cursor.fetchall()

    conn.close()
    return render_template('teacher/profile.html', teacher=teacher, subjects=subjects)


@main_bp.route('/students', methods=['GET', 'POST'])
@login_required(role='teacher')
def students():
    teacher_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor()

    if request.method == 'POST':
        action = request.form.get('action')

        try:
            if action == 'add':
                cursor.execute(
                    '''
                    INSERT INTO students (teacher_id, roll_no, name, father_name, mother_name, phone)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        teacher_id,
                        int(request.form['roll_no']),
                        request.form['name'].strip(),
                        request.form.get('father_name', '').strip(),
                        request.form.get('mother_name', '').strip(),
                        request.form.get('phone', '').strip(),
                    ),
                )
                conn.commit()
                flash('Student added successfully.', 'success')

            elif action == 'edit':
                cursor.execute(
                    '''
                    UPDATE students
                    SET roll_no = ?, name = ?, father_name = ?, mother_name = ?, phone = ?
                    WHERE id = ? AND teacher_id = ?
                    ''',
                    (
                        int(request.form['roll_no']),
                        request.form['name'].strip(),
                        request.form.get('father_name', '').strip(),
                        request.form.get('mother_name', '').strip(),
                        request.form.get('phone', '').strip(),
                        int(request.form['student_id']),
                        teacher_id,
                    ),
                )
                conn.commit()
                flash('Student updated successfully.', 'success')

            elif action == 'delete':
                _delete_student_records(cursor, teacher_id, int(request.form['student_id']))
                conn.commit()
                flash('Student deleted successfully.', 'success')

            elif action == 'import_excel' and 'file' in request.files:
                file = request.files['file']
                if file.filename.lower().endswith('.xlsx'):
                    count = import_students_from_excel(file, teacher_id)
                    conn.commit()
                    flash(f'{count} students imported successfully.', 'success')
                else:
                    flash('Please upload a valid .xlsx file.', 'danger')
        except ValueError as error:
            flash(str(error), 'danger')
        except sqlite3.IntegrityError:
            flash('Roll number already exists for this teacher.', 'danger')

    cursor.execute("SELECT * FROM students WHERE teacher_id = ? ORDER BY roll_no", (teacher_id,))
    students_list = cursor.fetchall()
    conn.close()

    return render_template('teacher/students.html', students=students_list)


@main_bp.route('/student/<int:student_id>')
@login_required(role='teacher')
def student_profile(student_id):
    teacher_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor()

    student, exam_history = _build_student_history(cursor, teacher_id, student_id)
    if not student:
        conn.close()
        flash('Student not found.', 'danger')
        return redirect(url_for('main.students'))

    cursor.execute("SELECT COUNT(*) AS total FROM attendance WHERE teacher_id = ? AND student_id = ?", (teacher_id, student_id))
    total_attendance = cursor.fetchone()['total']

    cursor.execute(
        '''
        SELECT COUNT(*) AS present
        FROM attendance
        WHERE teacher_id = ? AND student_id = ? AND status = 'present'
        ''',
        (teacher_id, student_id),
    )
    present_attendance = cursor.fetchone()['present']
    attendance_pct = round((present_attendance / total_attendance) * 100, 2) if total_attendance else 0

    cursor.execute("SELECT * FROM fees WHERE teacher_id = ? AND student_id = ?", (teacher_id, student_id))
    fee = cursor.fetchone()

    conn.close()
    return render_template(
        'teacher/student_profile.html',
        student=student,
        attendance_pct=attendance_pct,
        fee=fee,
        exam_history=exam_history,
    )


@main_bp.route('/subjects', methods=['GET', 'POST'])
@login_required(role='teacher')
def subjects():
    teacher_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor()

    if request.method == 'POST':
        action = request.form.get('action')
        subject_name = request.form.get('name', '').strip()

        try:
            if action == 'add' and subject_name:
                cursor.execute("INSERT INTO subjects (teacher_id, name) VALUES (?, ?)", (teacher_id, subject_name))
                conn.commit()
                flash('Subject added successfully.', 'success')

            elif action == 'edit':
                cursor.execute(
                    "UPDATE subjects SET name = ? WHERE id = ? AND teacher_id = ?",
                    (subject_name, int(request.form['subject_id']), teacher_id),
                )
                conn.commit()
                flash('Subject updated successfully.', 'success')

            elif action == 'delete':
                subject_id = int(request.form['subject_id'])
                cursor.execute("UPDATE exams SET subject_id = NULL WHERE teacher_id = ? AND subject_id = ?", (teacher_id, subject_id))
                cursor.execute("DELETE FROM subjects WHERE id = ? AND teacher_id = ?", (subject_id, teacher_id))
                conn.commit()
                flash('Subject deleted successfully.', 'success')
        except sqlite3.IntegrityError:
            flash('Subject already exists.', 'danger')

    cursor.execute(
        '''
        SELECT s.*, COUNT(DISTINCT st.id) AS student_count, COUNT(DISTINCT e.id) AS exam_count
        FROM subjects s
        LEFT JOIN students st ON st.teacher_id = s.teacher_id
        LEFT JOIN exams e ON e.subject_id = s.id
        WHERE s.teacher_id = ?
        GROUP BY s.id
        ORDER BY s.name
        ''',
        (teacher_id,),
    )
    subjects_list = cursor.fetchall()
    conn.close()
    return render_template('teacher/subjects.html', subjects=subjects_list)


@main_bp.route('/attendance', methods=['GET', 'POST'])
@login_required(role='teacher')
def attendance():
    teacher_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor()

    selected_date = request.args.get('date', date.today().isoformat())
    selected_month = request.args.get('month', selected_date[:7])

    if request.method == 'POST':
        attendance_date = request.form['date']
        student_ids = request.form.getlist('student_id')

        for student_id in student_ids:
            status = request.form.get(f'status_{student_id}', 'present')
            cursor.execute(
                '''
                INSERT INTO attendance (teacher_id, student_id, date, status)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(teacher_id, student_id, date) DO UPDATE SET
                    status = excluded.status
                ''',
                (teacher_id, int(student_id), attendance_date, status),
            )

        conn.commit()
        flash('Attendance saved successfully.', 'success')
        return redirect(url_for('main.attendance', date=attendance_date, month=attendance_date[:7]))

    cursor.execute("SELECT * FROM students WHERE teacher_id = ? ORDER BY roll_no", (teacher_id,))
    students_list = cursor.fetchall()

    cursor.execute(
        '''
        SELECT student_id, status
        FROM attendance
        WHERE teacher_id = ? AND date = ?
        ''',
        (teacher_id, selected_date),
    )
    attendance_records = {row['student_id']: row['status'] for row in cursor.fetchall()}

    cursor.execute(
        '''
        SELECT
            s.id,
            s.roll_no,
            s.name,
            COUNT(a.id) AS total_days,
            COALESCE(SUM(CASE WHEN a.status = 'present' THEN 1 ELSE 0 END), 0) AS present_days
        FROM students s
        LEFT JOIN attendance a
            ON a.student_id = s.id
            AND a.teacher_id = s.teacher_id
            AND strftime('%Y-%m', a.date) = ?
        WHERE s.teacher_id = ?
        GROUP BY s.id
        ORDER BY s.roll_no
        ''',
        (selected_month, teacher_id),
    )

    monthly_summary = []
    for row in cursor.fetchall():
        total_days = row['total_days']
        present_days = row['present_days']
        monthly_summary.append(
            {
                **dict(row),
                'attendance_pct': round((present_days / total_days) * 100, 2) if total_days else 0,
            }
        )

    conn.close()
    return render_template(
        'teacher/attendance.html',
        students=students_list,
        attendance=attendance_records,
        today=selected_date,
        selected_month=selected_month,
        monthly_summary=monthly_summary,
    )


@main_bp.route('/exams', methods=['GET', 'POST'])
@login_required(role='teacher')
def exams():
    teacher_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor()

    if request.method == 'POST':
        action = request.form.get('action', 'create')

        if action == 'create':
            cursor.execute(
                '''
                INSERT INTO exams (teacher_id, name, max_marks, exam_date)
                VALUES (?, ?, ?, ?)
                ''',
                (
                    teacher_id,
                    request.form['name'].strip(),
                    float(request.form['max_marks']),
                    request.form.get('exam_date') or None,
                ),
            )
            conn.commit()
            flash('Exam created successfully.', 'success')

        elif action == 'edit':
            cursor.execute(
                '''
                UPDATE exams
                SET name = ?, max_marks = ?, exam_date = ?
                WHERE id = ? AND teacher_id = ?
                ''',
                (
                    request.form['name'].strip(),
                    float(request.form['max_marks']),
                    request.form.get('exam_date') or None,
                    int(request.form['exam_id']),
                    teacher_id,
                ),
            )
            conn.commit()
            flash('Exam updated successfully.', 'success')

        elif action == 'delete':
            exam_id = int(request.form['exam_id'])
            cursor.execute("DELETE FROM marks WHERE exam_id = ?", (exam_id,))
            cursor.execute("DELETE FROM exams WHERE id = ? AND teacher_id = ?", (exam_id, teacher_id))
            conn.commit()
            flash('Exam deleted successfully.', 'success')

    cursor.execute(
        '''
        SELECT *
        FROM exams
        WHERE teacher_id = ?
        ORDER BY COALESCE(exam_date, created_at) DESC
        ''',
        (teacher_id,),
    )
    exams_list = cursor.fetchall()

    cursor.execute("SELECT * FROM subjects WHERE teacher_id = ? ORDER BY name", (teacher_id,))
    subjects_list = cursor.fetchall()

    conn.close()
    return render_template('teacher/exams.html', exams=exams_list, subjects=subjects_list)


@main_bp.route('/exam/<int:exam_id>/marks', methods=['GET', 'POST'])
@login_required(role='teacher')
def enter_marks(exam_id):
    teacher_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM exams WHERE id = ? AND teacher_id = ?", (exam_id, teacher_id))
    exam = cursor.fetchone()
    if not exam:
        conn.close()
        flash('Exam not found.', 'danger')
        return redirect(url_for('main.exams'))

    subjects, students_list, existing_marks = _build_mark_matrix(cursor, teacher_id, exam_id)

    if request.method == 'POST':
        if not subjects:
            conn.close()
            flash('Add at least one subject before entering marks.', 'danger')
            return redirect(url_for('main.subjects'))

        for student in students_list:
            for subject in subjects:
                field_name = f'mark_{student["id"]}_{subject["id"]}'
                raw_value = request.form.get(field_name, '').strip()
                if raw_value:
                    mark_value = float(raw_value)
                    if mark_value < 0 or mark_value > float(exam['max_marks']):
                        conn.close()
                        flash(
                            f'Marks for {student["name"]} in {subject["name"]} must be between 0 and {exam["max_marks"]}.',
                            'danger',
                        )
                        return redirect(url_for('main.enter_marks', exam_id=exam_id))

                    cursor.execute(
                        '''
                        INSERT INTO marks (exam_id, student_id, subject_id, marks_obtained)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(exam_id, student_id, subject_id) DO UPDATE SET
                            marks_obtained = excluded.marks_obtained
                        ''',
                        (exam_id, student['id'], subject['id'], mark_value),
                    )
                else:
                    cursor.execute(
                        '''
                        DELETE FROM marks
                        WHERE exam_id = ? AND student_id = ? AND subject_id = ?
                        ''',
                        (exam_id, student['id'], subject['id']),
                    )

        conn.commit()
        flash('All marks saved successfully.', 'success')
        return redirect(url_for('main.enter_marks', exam_id=exam_id))

    conn.close()
    return render_template(
        'teacher/enter_marks.html',
        exam=exam,
        students=students_list,
        subjects=subjects,
        existing_marks=existing_marks,
    )


@main_bp.route('/exam/<int:exam_id>/results')
@login_required(role='teacher')
def exam_results(exam_id):
    return redirect(url_for('main.results', exam_id=exam_id))


@main_bp.route('/results', methods=['GET'])
@login_required(role='teacher')
def results():
    teacher_id = session['user_id']
    exam_id = request.args.get('exam_id', type=int)
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        '''
        SELECT *
        FROM exams
        WHERE teacher_id = ?
        ORDER BY COALESCE(exam_date, created_at) DESC, created_at DESC
        ''',
        (teacher_id,),
    )
    exams_list = cursor.fetchall()

    selected_exam = None
    results = []
    subjects = []
    if exam_id:
        selected_exam, results = _build_exam_results(cursor, teacher_id, exam_id)
        if not selected_exam:
            conn.close()
            flash('Exam not found.', 'danger')
            return redirect(url_for('main.results'))
        subjects = _get_teacher_subjects(cursor, teacher_id)

    conn.close()
    return render_template(
        'teacher/results.html',
        exams=exams_list,
        selected_exam=selected_exam,
        subjects=subjects,
        results=results,
        selected_exam_id=exam_id,
    )


@main_bp.route('/fees', methods=['GET', 'POST'])
@login_required(role='teacher')
def fees():
    teacher_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor()

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'set_annual':
            student_id = int(request.form['student_id'])
            annual_fee = float(request.form['annual_fee'])
            cursor.execute(
                '''
                INSERT INTO fees (teacher_id, student_id, annual_fee, paid_amount)
                VALUES (?, ?, ?, COALESCE((SELECT paid_amount FROM fees WHERE teacher_id = ? AND student_id = ?), 0))
                ON CONFLICT(teacher_id, student_id) DO UPDATE SET
                    annual_fee = excluded.annual_fee,
                    paid_amount = excluded.paid_amount,
                    updated_at = CURRENT_TIMESTAMP
                ''',
                (teacher_id, student_id, annual_fee, teacher_id, student_id),
            )
            conn.commit()
            flash('Annual fee updated successfully.', 'success')

        elif action == 'collect':
            fee_id = int(request.form['fee_id'])
            amount = float(request.form['amount'])
            cursor.execute("SELECT annual_fee, paid_amount FROM fees WHERE id = ? AND teacher_id = ?", (fee_id, teacher_id))
            fee_row = cursor.fetchone()
            if not fee_row:
                flash('Fee record not found.', 'danger')
            else:
                balance = max(float(fee_row['annual_fee']) - float(fee_row['paid_amount']), 0)
                if amount <= 0 or amount > balance:
                    flash('Collected amount must be greater than zero and not exceed the balance.', 'danger')
                else:
                    cursor.execute(
                        "UPDATE fees SET paid_amount = paid_amount + ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (amount, fee_id),
                    )
                    cursor.execute("INSERT INTO fee_transactions (fee_id, amount) VALUES (?, ?)", (fee_id, amount))
                    conn.commit()
                    flash('Fee collected successfully.', 'success')

    cursor.execute(
        '''
        SELECT
            s.id AS student_id,
            s.roll_no,
            s.name,
            COALESCE(f.id, 0) AS fee_id,
            COALESCE(f.annual_fee, 0) AS annual_fee,
            COALESCE(f.paid_amount, 0) AS paid_amount,
            COALESCE(f.annual_fee - f.paid_amount, 0) AS balance
        FROM students s
        LEFT JOIN fees f ON s.id = f.student_id AND f.teacher_id = s.teacher_id
        WHERE s.teacher_id = ?
        ORDER BY s.roll_no
        ''',
        (teacher_id,),
    )
    fees_list = cursor.fetchall()

    conn.close()
    return render_template('teacher/fees.html', fees=fees_list)


@main_bp.route('/notes', methods=['GET', 'POST'])
@login_required(role='teacher')
def notes():
    teacher_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor()

    if request.method == 'POST':
        title = request.form['title'].strip()
        file = request.files['file']
        allowed = ('.pdf', '.docx', '.ppt', '.pptx')

        if file and file.filename.lower().endswith(allowed):
            try:
                filepath = _save_upload(file, 'notes')
                cursor.execute(
                    '''
                    INSERT INTO notes (teacher_id, title, filename, file_path)
                    VALUES (?, ?, ?, ?)
                    ''',
                    (teacher_id, title, secure_filename(file.filename), filepath),
                )
                conn.commit()
                flash('Note uploaded successfully.', 'success')
            except ValueError:
                flash('Invalid file name.', 'danger')
        else:
            flash('Allowed file types: PDF, DOCX, PPT, PPTX.', 'danger')

    cursor.execute("SELECT * FROM notes WHERE teacher_id = ? ORDER BY uploaded_at DESC", (teacher_id,))
    notes_list = cursor.fetchall()
    conn.close()
    return render_template('teacher/notes.html', notes=notes_list)


@main_bp.route('/papers', methods=['GET', 'POST'])
@login_required(role='teacher')
def papers():
    teacher_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor()

    if request.method == 'POST':
        title = request.form['title'].strip()
        file = request.files['file']
        allowed = ('.pdf', '.docx')

        if file and file.filename.lower().endswith(allowed):
            try:
                filepath = _save_upload(file, 'papers')
                cursor.execute(
                    '''
                    INSERT INTO question_papers (teacher_id, title, filename, file_path)
                    VALUES (?, ?, ?, ?)
                    ''',
                    (teacher_id, title, secure_filename(file.filename), filepath),
                )
                conn.commit()
                flash('Question paper uploaded successfully.', 'success')
            except ValueError:
                flash('Invalid file name.', 'danger')
        else:
            flash('Allowed file types: PDF and DOCX.', 'danger')

    cursor.execute("SELECT * FROM question_papers WHERE teacher_id = ? ORDER BY uploaded_at DESC", (teacher_id,))
    papers_list = cursor.fetchall()
    conn.close()
    return render_template('teacher/papers.html', papers=papers_list)


@main_bp.route('/reports')
@login_required(role='teacher')
def reports():
    teacher_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        '''
        SELECT *
        FROM exams
        WHERE teacher_id = ?
        ORDER BY COALESCE(exam_date, created_at) DESC, created_at DESC
        ''',
        (teacher_id,),
    )
    exams_list = cursor.fetchall()
    conn.close()
    return render_template('teacher/reports.html', exams=exams_list)


@main_bp.route('/reports/attendance')
@login_required(role='teacher')
def report_attendance():
    month = request.args.get('month')
    return export_attendance(session['user_id'], month=month)


@main_bp.route('/reports/marks')
@login_required(role='teacher')
def report_exam_result():
    exam_id = request.args.get('exam_id', type=int)
    if not exam_id:
        flash('Select an exam first.', 'warning')
        return redirect(url_for('main.results'))
    return export_marks(session['user_id'], exam_id=exam_id)


@main_bp.route('/reports/fees')
@login_required(role='teacher')
def report_fees():
    return export_fees(session['user_id'])


@main_bp.route('/reports/students')
@login_required(role='teacher')
def report_students():
    return export_student_report(session['user_id'])
