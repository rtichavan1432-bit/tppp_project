import os
import sqlite3

from werkzeug.security import generate_password_hash

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'database', 'school.db')


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_column(cursor, table_name, column_name, definition):
    cursor.execute(f"PRAGMA table_info({table_name})")
    existing_columns = {row["name"] for row in cursor.fetchall()}
    if column_name not in existing_columns:
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def _is_password_hash(value):
    return isinstance(value, str) and (
        value.startswith('pbkdf2:')
        or value.startswith('scrypt:')
        or value.startswith('argon2:')
    )


def _rebuild_marks_table(cursor):
    cursor.execute("PRAGMA table_info(marks)")
    columns = {row["name"] for row in cursor.fetchall()}
    if "subject_id" in columns:
        return

    cursor.execute("ALTER TABLE marks RENAME TO marks_legacy")
    cursor.execute(
        '''
        CREATE TABLE marks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            subject_id INTEGER,
            marks_obtained REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (exam_id) REFERENCES exams(id),
            FOREIGN KEY (student_id) REFERENCES students(id),
            FOREIGN KEY (subject_id) REFERENCES subjects(id)
        )
        '''
    )
    cursor.execute(
        '''
        INSERT INTO marks (exam_id, student_id, subject_id, marks_obtained)
        SELECT m.exam_id, m.student_id, e.subject_id, m.marks_obtained
        FROM marks_legacy m
        LEFT JOIN exams e ON e.id = m.exam_id
        '''
    )
    cursor.execute("DROP TABLE marks_legacy")


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS admin (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        '''
    )

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL,
            teacher_id INTEGER,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        '''
    )

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS teachers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            name TEXT,
            school_name TEXT,
            mobile TEXT,
            class_name TEXT,
            division TEXT,
            academic_year TEXT,
            is_active INTEGER DEFAULT 1,
            is_setup_complete INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        '''
    )

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        )
        '''
    )

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            roll_no INTEGER NOT NULL,
            name TEXT NOT NULL,
            father_name TEXT,
            mother_name TEXT,
            phone TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        )
        '''
    )

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            date DATE NOT NULL,
            status TEXT NOT NULL,
            FOREIGN KEY (teacher_id) REFERENCES teachers(id),
            FOREIGN KEY (student_id) REFERENCES students(id),
            UNIQUE(teacher_id, student_id, date)
        )
        '''
    )

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS exams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            max_marks REAL NOT NULL,
            subject_id INTEGER,
            exam_date DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (teacher_id) REFERENCES teachers(id),
            FOREIGN KEY (subject_id) REFERENCES subjects(id)
        )
        '''
    )

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS marks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            subject_id INTEGER,
            marks_obtained REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (exam_id) REFERENCES exams(id),
            FOREIGN KEY (student_id) REFERENCES students(id),
            FOREIGN KEY (subject_id) REFERENCES subjects(id),
            UNIQUE(exam_id, student_id, subject_id)
        )
        '''
    )

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS fees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            annual_fee REAL NOT NULL,
            paid_amount REAL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (teacher_id) REFERENCES teachers(id),
            FOREIGN KEY (student_id) REFERENCES students(id)
        )
        '''
    )

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS fee_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fee_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            transaction_date DATE DEFAULT CURRENT_DATE,
            FOREIGN KEY (fee_id) REFERENCES fees(id)
        )
        '''
    )

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        )
        '''
    )

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS question_papers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        )
        '''
    )

    _ensure_column(cursor, 'teachers', 'academic_year', 'TEXT')
    _ensure_column(cursor, 'students', 'father_name', 'TEXT')
    _ensure_column(cursor, 'students', 'mother_name', 'TEXT')
    _ensure_column(cursor, 'students', 'phone', 'TEXT')
    _rebuild_marks_table(cursor)
    cursor.execute(
        '''
        DELETE FROM fees
        WHERE id NOT IN (
            SELECT MAX(id)
            FROM fees
            GROUP BY teacher_id, student_id
        )
        '''
    )
    cursor.execute(
        '''
        DELETE FROM students
        WHERE id NOT IN (
            SELECT MAX(id)
            FROM students
            GROUP BY teacher_id, roll_no
        )
        '''
    )
    cursor.execute(
        '''
        DELETE FROM subjects
        WHERE id NOT IN (
            SELECT MAX(id)
            FROM subjects
            GROUP BY teacher_id, name
        )
        '''
    )
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_fees_teacher_student ON fees(teacher_id, student_id)')
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_students_teacher_roll ON students(teacher_id, roll_no)')
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_subjects_teacher_name ON subjects(teacher_id, name)')
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_marks_exam_student_subject ON marks(exam_id, student_id, subject_id)')
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username)')

    default_admin_hash = generate_password_hash('admin123')
    cursor.execute(
        '''
        INSERT OR IGNORE INTO admin (id, username, password)
        VALUES (1, 'admin', ?)
        ''',
        (default_admin_hash,),
    )

    cursor.execute("SELECT id, password FROM admin WHERE username = 'admin'")
    admin_row = cursor.fetchone()
    if admin_row and not _is_password_hash(admin_row['password']):
        cursor.execute("UPDATE admin SET password = ? WHERE id = ?", (generate_password_hash(admin_row['password']), admin_row['id']))

    cursor.execute("SELECT id, username, password, is_active FROM teachers")
    teachers = cursor.fetchall()
    for teacher in teachers:
        password = teacher['password']
        if not _is_password_hash(password):
            password = generate_password_hash(password)
            cursor.execute("UPDATE teachers SET password = ? WHERE id = ?", (password, teacher['id']))

        cursor.execute(
            '''
            INSERT INTO users (username, password, role, teacher_id, is_active)
            VALUES (?, ?, 'teacher', ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                password = excluded.password,
                role = excluded.role,
                teacher_id = excluded.teacher_id,
                is_active = excluded.is_active
            ''',
            (teacher['username'], password, teacher['id'], teacher['is_active']),
        )

    cursor.execute("SELECT id, username, password FROM admin")
    admins = cursor.fetchall()
    for admin in admins:
        cursor.execute(
            '''
            INSERT INTO users (username, password, role, teacher_id, is_active)
            VALUES (?, ?, 'admin', NULL, 1)
            ON CONFLICT(username) DO UPDATE SET
                password = excluded.password,
                role = excluded.role,
                teacher_id = NULL,
                is_active = 1
            ''',
            (admin['username'], admin['password']),
        )

    conn.commit()
    conn.close()
    print("Database initialized successfully!")


if __name__ == "__main__":
    init_db()
