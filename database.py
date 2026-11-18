import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


DATABASE_PATH = Path(
    os.getenv("DATABASE_PATH")
    or Path(__file__).resolve().with_name("svcn_vidya_mithra.db")
)


@contextmanager
def get_connection():
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database():
    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                program TEXT NOT NULL,
                department TEXT NOT NULL DEFAULT '',
                study_year INTEGER NOT NULL DEFAULT 0,
                semester INTEGER NOT NULL DEFAULT 0,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS sessions_expiry_idx
                ON sessions(expires_at);

            CREATE TABLE IF NOT EXISTS admin_sessions (
                token_hash TEXT PRIMARY KEY,
                expires_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS admin_sessions_expiry_idx
                ON admin_sessions(expires_at);

            CREATE TABLE IF NOT EXISTS courses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                lessons_completed INTEGER NOT NULL DEFAULT 0 CHECK (lessons_completed >= 0),
                total_lessons INTEGER NOT NULL CHECK (total_lessons > 0),
                created_at INTEGER NOT NULL,
                UNIQUE (student_id, title COLLATE NOCASE),
                CHECK (lessons_completed <= total_lessons)
            );

            CREATE INDEX IF NOT EXISTS courses_student_idx
                ON courses(student_id, id);

            CREATE TABLE IF NOT EXISTS syllabi (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                department TEXT NOT NULL COLLATE NOCASE,
                study_year INTEGER NOT NULL CHECK (study_year BETWEEN 1 AND 4),
                semester INTEGER NOT NULL CHECK (semester BETWEEN 1 AND 8),
                subject_code TEXT NOT NULL COLLATE NOCASE,
                subject_name TEXT NOT NULL,
                syllabus_text TEXT NOT NULL,
                important_questions TEXT NOT NULL,
                source_filename TEXT NOT NULL DEFAULT '',
                updated_at INTEGER NOT NULL,
                UNIQUE (department, study_year, semester, subject_code)
            );

            CREATE INDEX IF NOT EXISTS syllabi_student_scope_idx
                ON syllabi(department, study_year, semester);
            """
        )

        student_columns = {
            column["name"] for column in connection.execute("PRAGMA table_info(students)")
        }
        for column_name, column_definition in (
            ("department", "TEXT NOT NULL DEFAULT ''"),
            ("study_year", "INTEGER NOT NULL DEFAULT 0"),
            ("semester", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if column_name not in student_columns:
                connection.execute(
                    f"ALTER TABLE students ADD COLUMN {column_name} {column_definition}"
                )


def create_student(
    name,
    email,
    program,
    department,
    study_year,
    semester,
    password_salt,
    password_hash,
    created_at,
):
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO students (
                name, email, program, department, study_year, semester,
                password_salt, password_hash, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                email,
                program,
                department,
                study_year,
                semester,
                password_salt,
                password_hash,
                created_at,
            ),
        )
        return connection.execute(
            """
            SELECT id, name, email, program, department, study_year, semester
            FROM students
            WHERE id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()


def find_student_by_email(email):
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT id, name, email, program, department, study_year, semester,
                   password_salt, password_hash
            FROM students
            WHERE email = ?
            """,
            (email,),
        ).fetchone()


def update_student_profile(student_id, department, study_year, semester):
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE students
            SET department = ?, study_year = ?, semester = ?
            WHERE id = ?
            """,
            (department, study_year, semester, student_id),
        )
        return connection.execute(
            """
            SELECT id, name, email, program, department, study_year, semester
            FROM students
            WHERE id = ?
            """,
            (student_id,),
        ).fetchone()


def create_session(token_hash, student_id, expires_at, now):
    with get_connection() as connection:
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        connection.execute(
            """
            INSERT INTO sessions (token_hash, student_id, expires_at)
            VALUES (?, ?, ?)
            """,
            (token_hash, student_id, expires_at),
        )


def create_admin_session(token_hash, expires_at, now):
    with get_connection() as connection:
        connection.execute("DELETE FROM admin_sessions WHERE expires_at <= ?", (now,))
        connection.execute(
            "INSERT INTO admin_sessions (token_hash, expires_at) VALUES (?, ?)",
            (token_hash, expires_at),
        )


def find_admin_session(token_hash, now):
    with get_connection() as connection:
        return connection.execute(
            "SELECT token_hash FROM admin_sessions WHERE token_hash = ? AND expires_at > ?",
            (token_hash, now),
        ).fetchone()


def delete_admin_session(token_hash):
    with get_connection() as connection:
        connection.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (token_hash,))


def find_student_by_session(token_hash, now):
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT students.id, students.name, students.email, students.program,
                   students.department, students.study_year, students.semester
            FROM sessions
            JOIN students ON students.id = sessions.student_id
            WHERE sessions.token_hash = ? AND sessions.expires_at > ?
            """,
            (token_hash, now),
        ).fetchone()


def delete_session(token_hash):
    with get_connection() as connection:
        connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


def _syllabus_response(record):
    return {
        "id": record["id"],
        "department": record["department"],
        "year": record["study_year"],
        "semester": record["semester"],
        "subjectCode": record["subject_code"],
        "subjectName": record["subject_name"],
        "syllabusText": record["syllabus_text"],
        "importantQuestions": record["important_questions"],
        "sourceFilename": record["source_filename"],
        "updatedAt": record["updated_at"],
    }


def upsert_syllabus(
    department,
    study_year,
    semester,
    subject_code,
    subject_name,
    syllabus_text,
    important_questions,
    source_filename,
    updated_at,
):
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO syllabi (
                department, study_year, semester, subject_code, subject_name,
                syllabus_text, important_questions, source_filename, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(department, study_year, semester, subject_code)
            DO UPDATE SET
                subject_name = excluded.subject_name,
                syllabus_text = excluded.syllabus_text,
                important_questions = excluded.important_questions,
                source_filename = excluded.source_filename,
                updated_at = excluded.updated_at
            """,
            (
                department,
                study_year,
                semester,
                subject_code,
                subject_name,
                syllabus_text,
                important_questions,
                source_filename,
                updated_at,
            ),
        )
        record = connection.execute(
            """
            SELECT * FROM syllabi
            WHERE department = ? AND study_year = ? AND semester = ? AND subject_code = ?
            """,
            (department, study_year, semester, subject_code),
        ).fetchone()
        return _syllabus_response(record)


def list_syllabi(department=None, study_year=None, semester=None):
    with get_connection() as connection:
        if department is None:
            records = connection.execute(
                "SELECT * FROM syllabi ORDER BY department, study_year, semester, subject_code"
            ).fetchall()
        else:
            records = connection.execute(
                """
                SELECT * FROM syllabi
                WHERE department = ? AND study_year = ? AND semester = ?
                ORDER BY subject_code
                """,
                (department, study_year, semester),
            ).fetchall()
        return [_syllabus_response(record) for record in records]


def delete_syllabus(syllabus_id):
    with get_connection() as connection:
        cursor = connection.execute("DELETE FROM syllabi WHERE id = ?", (syllabus_id,))
        return cursor.rowcount > 0


def _course_response(course):
    course_id = course["id"]
    title = course["title"]
    category = course["category"]
    description = f"{title} {category}".lower()
    if "machine" in description or "artificial intelligence" in description or " ai" in description:
        icon = "brain"
    elif "data" in description or "math" in description:
        icon = "chart"
    else:
        icon = "book"

    total_lessons = course["total_lessons"]
    lessons_completed = course["lessons_completed"]
    return {
        "id": course_id,
        "title": title,
        "category": category,
        "progress": round(lessons_completed * 100 / total_lessons),
        "lessonsCompleted": lessons_completed,
        "totalLessons": total_lessons,
        "color": ("violet", "mint", "coral")[(course_id - 1) % 3],
        "icon": icon,
    }


def create_course(student_id, title, category, total_lessons, created_at):
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO courses (student_id, title, category, total_lessons, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (student_id, title, category, total_lessons, created_at),
        )
        course = connection.execute(
            "SELECT * FROM courses WHERE id = ? AND student_id = ?",
            (cursor.lastrowid, student_id),
        ).fetchone()
        return _course_response(course)


def list_courses(student_id):
    with get_connection() as connection:
        courses = connection.execute(
            """
            SELECT * FROM courses
            WHERE student_id = ?
            ORDER BY created_at, id
            """,
            (student_id,),
        ).fetchall()
        return [_course_response(course) for course in courses]


def update_course_progress(student_id, course_id, lessons_completed):
    with get_connection() as connection:
        course = connection.execute(
            "SELECT * FROM courses WHERE id = ? AND student_id = ?",
            (course_id, student_id),
        ).fetchone()
        if course is None:
            return None
        if lessons_completed > course["total_lessons"]:
            return False

        connection.execute(
            """
            UPDATE courses SET lessons_completed = ?
            WHERE id = ? AND student_id = ?
            """,
            (lessons_completed, course_id, student_id),
        )
        updated_course = connection.execute(
            "SELECT * FROM courses WHERE id = ? AND student_id = ?",
            (course_id, student_id),
        ).fetchone()
        return _course_response(updated_course)
