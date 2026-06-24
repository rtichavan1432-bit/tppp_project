from io import BytesIO
from numbers import Number
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd
from flask import send_file

from models.database import get_db
from utils.auth import calculate_grade


def _column_name(index):
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _stringify(value):
    if pd.isna(value):
        return ""
    if isinstance(value, Number):
        return value
    return str(value)


def _build_xlsx_dataframe(df, sheet_name):
    rows = [list(df.columns)] + df.astype(object).where(pd.notna(df), None).values.tolist()
    row_count = len(rows)
    col_count = max((len(row) for row in rows), default=0)
    last_cell = f"{_column_name(col_count)}{row_count}" if row_count and col_count else "A1"

    sheet_rows = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for col_index, value in enumerate(row, start=1):
            cell_ref = f"{_column_name(col_index)}{row_index}"
            cell_value = _stringify(value)
            if isinstance(cell_value, Number):
                cells.append(f'<c r="{cell_ref}"><v>{cell_value}</v></c>')
            else:
                cells.append(
                    f'<c r="{cell_ref}" t="inlineStr"><is><t>{escape(str(cell_value))}</t></is></c>'
                )
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<dimension ref="A1:{last_cell}"/>'
        f'<sheetData>{"".join(sheet_rows)}</sheetData>'
        '</worksheet>'
    )

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets>'
        f'<sheet name="{escape(sheet_name[:31])}" sheetId="1" r:id="rId1"/>'
        '</sheets>'
        '</workbook>'
    )

    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>'
    )

    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        '</Relationships>'
    )

    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        '</Relationships>'
    )

    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types_xml)
        archive.writestr("_rels/.rels", rels_xml)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)

    output.seek(0)
    return output


def _column_index(cell_ref):
    letters = "".join(char for char in cell_ref if char.isalpha()).upper()
    index = 0
    for char in letters:
        index = (index * 26) + (ord(char) - 64)
    return index


def _xlsx_shared_strings(archive):
    shared_strings = []
    if "xl/sharedStrings.xml" not in archive.namelist():
        return shared_strings

    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    namespace = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    for item in root.findall("a:si", namespace):
        parts = [text_node.text or "" for text_node in item.findall(".//a:t", namespace)]
        shared_strings.append("".join(parts))
    return shared_strings


def _xlsx_first_sheet_path(archive):
    workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
    rels_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    workbook_ns = {
        "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    }
    rels_ns = {"a": "http://schemas.openxmlformats.org/package/2006/relationships"}

    sheet = workbook_root.find("a:sheets/a:sheet", workbook_ns)
    if sheet is None:
        raise ValueError("Excel file does not contain any sheets")

    rel_id = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
    for relation in rels_root.findall("a:Relationship", rels_ns):
        if relation.attrib.get("Id") == rel_id:
            return f"xl/{relation.attrib['Target'].lstrip('/')}"

    raise ValueError("Excel sheet relationship is missing")


def _xlsx_cell_value(cell, shared_strings, namespace):
    cell_type = cell.attrib.get("t")

    if cell_type == "inlineStr":
        return "".join(text_node.text or "" for text_node in cell.findall(".//a:t", namespace))

    value_node = cell.find("a:v", namespace)
    if value_node is None or value_node.text is None:
        return ""

    raw_value = value_node.text
    if cell_type == "s":
        return shared_strings[int(raw_value)]

    try:
        numeric = float(raw_value)
        return int(numeric) if numeric.is_integer() else numeric
    except ValueError:
        return raw_value


def _read_xlsx_rows(file):
    file.stream.seek(0)
    with ZipFile(file.stream) as archive:
        shared_strings = _xlsx_shared_strings(archive)
        sheet_path = _xlsx_first_sheet_path(archive)
        root = ET.fromstring(archive.read(sheet_path))

    namespace = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows = []
    for row in root.findall(".//a:sheetData/a:row", namespace):
        row_values = {}
        max_index = 0
        for cell in row.findall("a:c", namespace):
            cell_ref = cell.attrib.get("r", "")
            column_index = _column_index(cell_ref)
            if column_index == 0:
                continue
            row_values[column_index] = _xlsx_cell_value(cell, shared_strings, namespace)
            max_index = max(max_index, column_index)
        rows.append([row_values.get(index, "") for index in range(1, max_index + 1)])
    return rows


def _normalize_header(value):
    return str(value).strip().lower().replace("_", " ").replace("-", " ")


def _get_teacher_subjects(cursor, teacher_id):
    cursor.execute("SELECT * FROM subjects WHERE teacher_id = ? ORDER BY name", (teacher_id,))
    return cursor.fetchall()


def _get_teacher_students(cursor, teacher_id):
    cursor.execute("SELECT * FROM students WHERE teacher_id = ? ORDER BY roll_no", (teacher_id,))
    return cursor.fetchall()


def _build_exam_result_frame(cursor, teacher_id, exam_id):
    cursor.execute("SELECT * FROM exams WHERE id = ? AND teacher_id = ?", (exam_id, teacher_id))
    exam = cursor.fetchone()
    if not exam:
        raise ValueError("Exam not found")

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
    marks_map = {(row["student_id"], row["subject_id"]): row["marks_obtained"] for row in cursor.fetchall()}

    rows = []
    for student in students:
        row = {"Roll No": student["roll_no"], "Student Name": student["name"]}
        total = 0
        for subject in subjects:
            mark = marks_map.get((student["id"], subject["id"]))
            row[subject["name"]] = mark if mark is not None else ""
            total += float(mark or 0)

        total_possible = float(exam["max_marks"]) * len(subjects)
        percentage = round((total / total_possible) * 100, 2) if total_possible else 0
        grade = calculate_grade(percentage)[0]
        row["Total"] = round(total, 2)
        row["Percentage"] = percentage
        row["Grade"] = grade
        rows.append(row)

    rows.sort(key=lambda item: item["Total"], reverse=True)
    for rank, row in enumerate(rows, 1):
        row["Rank"] = rank

    columns = ["Roll No", "Student Name"] + [subject["name"] for subject in subjects] + ["Total", "Percentage", "Grade", "Rank"]
    return exam, pd.DataFrame(rows, columns=columns)


def _build_consolidated_student_frame(cursor, teacher_id):
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
    students = _get_teacher_students(cursor, teacher_id)

    cursor.execute(
        '''
        SELECT exam_id, student_id, subject_id, marks_obtained
        FROM marks
        WHERE student_id IN (SELECT id FROM students WHERE teacher_id = ?)
        ''',
        (teacher_id,),
    )

    marks_map = {}
    for row in cursor.fetchall():
        marks_map.setdefault(row["exam_id"], {}).setdefault(row["student_id"], {})[row["subject_id"]] = row["marks_obtained"]

    rows = []
    for exam in exams:
        for student in students:
            row = {
                "Exam Name": exam["name"],
                "Roll No": student["roll_no"],
                "Student Name": student["name"],
            }
            total = 0
            for subject in subjects:
                mark = marks_map.get(exam["id"], {}).get(student["id"], {}).get(subject["id"])
                row[subject["name"]] = mark if mark is not None else ""
                total += float(mark or 0)

            total_possible = float(exam["max_marks"]) * len(subjects)
            percentage = round((total / total_possible) * 100, 2) if total_possible else 0
            row["Total"] = round(total, 2)
            row["Percentage"] = percentage
            row["Grade"] = calculate_grade(percentage)[0]
            rows.append(row)

    columns = ["Exam Name", "Roll No", "Student Name"] + [subject["name"] for subject in subjects] + ["Total", "Percentage", "Grade"]
    return pd.DataFrame(rows, columns=columns)


def export_attendance(teacher_id, month=None):
    conn = get_db()
    query = '''
        SELECT s.roll_no, s.name, a.date, a.status
        FROM attendance a
        JOIN students s ON a.student_id = s.id
        WHERE a.teacher_id = ?
    '''
    params = [teacher_id]
    if month:
        query += " AND strftime('%Y-%m', a.date) = ?"
        params.append(month)
    query += " ORDER BY a.date, s.roll_no"

    df = pd.read_sql_query(query, conn, params=params)
    conn.close()

    output = _build_xlsx_dataframe(df, "Attendance")
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='Attendance.xlsx',
    )


def export_marks(teacher_id, exam_id=None):
    if not exam_id:
        raise ValueError("An exam must be selected to export the result report")

    conn = get_db()
    cursor = conn.cursor()
    exam, df = _build_exam_result_frame(cursor, teacher_id, exam_id)
    conn.close()

    output = _build_xlsx_dataframe(df, "Exam Result")
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='Exam_Result.xlsx',
    )


def export_fees(teacher_id):
    conn = get_db()
    query = '''
        SELECT s.roll_no, s.name, f.annual_fee, f.paid_amount, (f.annual_fee - f.paid_amount) AS remaining_fee
        FROM fees f
        JOIN students s ON f.student_id = s.id
        WHERE f.teacher_id = ?
        ORDER BY s.roll_no
    '''
    df = pd.read_sql_query(query, conn, params=[teacher_id])
    conn.close()

    output = _build_xlsx_dataframe(df, "Fees")
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='Fees.xlsx',
    )


def export_student_report(teacher_id):
    conn = get_db()
    cursor = conn.cursor()
    df = _build_consolidated_student_frame(cursor, teacher_id)
    conn.close()

    output = _build_xlsx_dataframe(df, "Consolidated Result")
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='Student_Report.xlsx',
    )


def import_students_from_excel(file, teacher_id):
    if not file.filename.lower().endswith(".xlsx"):
        raise ValueError("Only .xlsx files are supported in this environment")

    rows = _read_xlsx_rows(file)
    if not rows:
        raise ValueError("Excel file is empty")

    headers = [_normalize_header(value) for value in rows[0]]
    required = {"roll no": None, "name": None}
    optional_aliases = {
        "father_name": {"father name", "father"},
        "mother_name": {"mother name", "mother"},
        "phone": {"phone", "phone number", "mobile", "mobile number"},
    }

    for index, header in enumerate(headers):
        if header in required:
            required[header] = index

    if any(index is None for index in required.values()):
        raise ValueError("Excel must contain 'Roll No' and 'Name' columns")

    optional_indexes = {}
    for key, aliases in optional_aliases.items():
        optional_indexes[key] = next((idx for idx, header in enumerate(headers) if header in aliases), None)

    conn = get_db()
    cursor = conn.cursor()
    imported_count = 0

    for row in rows[1:]:
        roll_value = row[required["roll no"]] if required["roll no"] < len(row) else ""
        name_value = row[required["name"]] if required["name"] < len(row) else ""

        if roll_value in ("", None) and name_value in ("", None):
            continue
        if roll_value in ("", None) or name_value in ("", None):
            raise ValueError("Each student row must include both roll number and name")

        father_value = ""
        mother_value = ""
        phone_value = ""
        if optional_indexes["father_name"] is not None and optional_indexes["father_name"] < len(row):
            father_value = str(row[optional_indexes["father_name"]]).strip()
        if optional_indexes["mother_name"] is not None and optional_indexes["mother_name"] < len(row):
            mother_value = str(row[optional_indexes["mother_name"]]).strip()
        if optional_indexes["phone"] is not None and optional_indexes["phone"] < len(row):
            phone_value = str(row[optional_indexes["phone"]]).strip()

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
            (teacher_id, int(roll_value), str(name_value).strip(), father_value, mother_value, phone_value),
        )
        imported_count += 1

    return imported_count
