
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3

app = FastAPI(title="SVCN Vidya Mithra")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    conn = sqlite3.connect("edumind.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            roll_no TEXT UNIQUE NOT NULL,
            department TEXT,
            year TEXT,
            semester TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

class Student(BaseModel):
    name: str
    roll_no: str
    department: str
    year: str
    semester: str

class QuizSubmission(BaseModel):
    student_name: str
    answers: list[int]

class TutorQuestion(BaseModel):
    question: str
    language: str = "English"

@app.get("/")
def home():
    return {"message": "Welcome to SVCN Vidya Mithra"}

@app.get("/api/health")
def health():
    return {"status": "Backend connected"}

@app.post("/api/students")
def add_student(student: Student):
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO students
            (name, roll_no, department, year, semester)
            VALUES (?, ?, ?, ?, ?)""",
            (student.name, student.roll_no, student.department,
             student.year, student.semester)
        )
        conn.commit()
        return {"message": "Student added successfully"}
    except sqlite3.IntegrityError:
        return {"error": "Roll number already exists"}
    finally:
        conn.close()

@app.get("/api/students")
def get_students():
    conn = get_db()
    rows = conn.execute("SELECT * FROM students").fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.post("/api/quiz")
def submit_quiz(data: QuizSubmission):
    correct_answers = [1, 0, 2, 1, 2]
    total = min(len(data.answers), len(correct_answers))
    score = sum(
        data.answers[i] == correct_answers[i]
        for i in range(total)
    )
    percentage = round(score / total * 100) if total else 0

    if percentage >= 80:
        level = "Advanced"
    elif percentage >= 50:
        level = "Intermediate"
    else:
        level = "Beginner"

    return {
        "student": data.student_name,
        "score": score,
        "total": total,
        "percentage": percentage,
        "level": level,
        "note": "This is an initial estimate, not a final measure of ability."
    }

@app.post("/api/tutor")
def tutor(data: TutorQuestion):
    if data.language.lower() == "telugu":
        answer = (
            "Mee question: " + data.question +
            "\nDheenini simple steps lo explain cheyyali. "
            "Topic name and syllabus content add chesthe "
            "accurate lesson generate cheyyagalamu."
        )
    else:
        answer = (
            "Let's understand your question step by step: "
            + data.question +
            ". Please add the relevant syllabus notes for a "
            "detailed syllabus-based explanation."
        )

    return {"answer": answer}
