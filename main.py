import hashlib
import hmac
import io
import logging
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path

import database
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from pypdf import PdfReader
from pypdf.errors import PdfReadError

load_dotenv(Path(__file__).with_name(".env"))

app = FastAPI(title="SVCN Vidya Mithra API", version="0.1.0")
logger = logging.getLogger(__name__)
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_SYLLABUS_CHARACTERS = 200_000
CORS_ORIGINS = [
    origin.strip().rstrip("/")
    for origin in os.getenv("CORS_ORIGINS", "").split(",")
    if origin.strip()
] or ["http://localhost:5173", "http://127.0.0.1:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

database.initialize_database()


class AcademicScope(BaseModel):
    department: str = Field(min_length=2, max_length=80)
    year: int = Field(ge=1, le=4)
    semester: int = Field(ge=1, le=8)

    @field_validator("department")
    @classmethod
    def normalize_department(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Department cannot be blank.")
        return value.upper()

    @model_validator(mode="after")
    def semester_matches_year(self):
        expected_semesters = {self.year * 2 - 1, self.year * 2}
        if self.semester not in expected_semesters:
            raise ValueError("Semester must match the selected B.Tech year.")
        return self


class RegisterRequest(AcademicScope):
    name: str = Field(min_length=2, max_length=80)
    email: str = Field(max_length=254)
    password: str = Field(min_length=8, max_length=128)
    program: str = Field(default="General Studies", min_length=2, max_length=100)

    @field_validator("name", "program")
    @classmethod
    def trim_text(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank.")
        return value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Enter a valid email address.")
        return value


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        return value.strip().lower()


class StudentProfileRequest(AcademicScope):
    pass


class AdminLoginRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        return value.strip().lower()


class SyllabusFields(AcademicScope):
    subject_code: str = Field(min_length=1, max_length=30)
    subject_name: str = Field(min_length=2, max_length=120)
    important_questions: str = Field(min_length=1, max_length=100_000)

    @field_validator("subject_code")
    @classmethod
    def normalize_subject_code(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Subject code cannot be blank.")
        return value.upper()

    @field_validator("subject_name", "important_questions")
    @classmethod
    def trim_syllabus_fields(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank.")
        return value


class SyllabusEntryRequest(SyllabusFields):
    syllabus_text: str = Field(min_length=1, max_length=MAX_SYLLABUS_CHARACTERS)

    @field_validator("syllabus_text")
    @classmethod
    def trim_syllabus_text(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Syllabus content cannot be blank.")
        return value


class CourseRequest(BaseModel):
    title: str = Field(min_length=2, max_length=100)
    category: str = Field(default="General", min_length=2, max_length=80)
    total_lessons: int = Field(ge=1, le=500)

    @field_validator("title", "category")
    @classmethod
    def trim_course_text(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank.")
        return value


class CourseProgressRequest(BaseModel):
    lessons_completed: int = Field(ge=0)


def hash_password(password, salt):
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=bytes.fromhex(salt),
        n=2**14,
        r=8,
        p=1,
        dklen=64,
    ).hex()


def create_access_token(student_id):
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    database.create_session(
        hashlib.sha256(token.encode("utf-8")).hexdigest(),
        student_id,
        now + 60 * 60 * 24 * 7,
        now,
    )
    return token


def public_student(student):
    name_parts = student["name"].split()
    initials = "".join(part[0] for part in name_parts[:2]).upper()
    year = student["study_year"]
    semester = student["semester"]
    return {
        "id": student["id"],
        "name": student["name"],
        "firstName": name_parts[0],
        "email": student["email"],
        "initials": initials,
        "program": student["program"],
        "department": student["department"],
        "year": year,
        "semester": semester,
        "profileComplete": bool(student["department"] and year and semester),
    }


def get_current_student(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Please sign in to continue.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    student = database.find_student_by_session(token_hash, int(time.time()))
    if student is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    return student


def create_admin_access_token():
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    database.create_admin_session(
        hashlib.sha256(token.encode("utf-8")).hexdigest(),
        now + 60 * 60 * 8,
        now,
    )
    return token


def get_current_admin(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Admin sign-in required.")

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Invalid or expired admin session.")

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if database.find_admin_session(token_hash, int(time.time())) is None:
        raise HTTPException(status_code=401, detail="Invalid or expired admin session.")
    return {"email": ADMIN_EMAIL}


def save_syllabus(fields, syllabus_text, source_filename=""):
    return database.upsert_syllabus(
        fields.department,
        fields.year,
        fields.semester,
        fields.subject_code,
        fields.subject_name,
        syllabus_text,
        fields.important_questions,
        source_filename,
        int(time.time()),
    )


@app.get("/")
def read_root():
    return {"message": "SVCN Vidya Mithra API is running"}


@app.post("/api/auth/register", status_code=201)
def register_student(request: RegisterRequest):
    salt = secrets.token_hex(16)
    try:
        student = database.create_student(
            request.name,
            request.email,
            request.program,
            request.department,
            request.year,
            request.semester,
            salt,
            hash_password(request.password, salt),
            int(time.time()),
        )
    except sqlite3.IntegrityError as error:
        if "UNIQUE constraint failed" not in str(error):
            raise
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    student = dict(student)
    token = create_access_token(student["id"])
    return {"access_token": token, "token_type": "bearer", "student": public_student(student)}


@app.post("/api/auth/login")
def login_student(request: LoginRequest):
    student = database.find_student_by_email(request.email)
    if student is None:
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")

    expected_hash = hash_password(request.password, student["password_salt"])
    if not hmac.compare_digest(expected_hash, student["password_hash"]):
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")

    student = dict(student)
    token = create_access_token(student["id"])
    return {"access_token": token, "token_type": "bearer", "student": public_student(student)}


@app.get("/api/auth/me")
def read_current_student(student=Depends(get_current_student)):
    return public_student(student)


@app.patch("/api/auth/profile")
def update_student_profile(
    request: StudentProfileRequest,
    student=Depends(get_current_student),
):
    updated_student = database.update_student_profile(
        student["id"],
        request.department,
        request.year,
        request.semester,
    )
    return public_student(updated_student)


@app.post("/api/auth/logout")
def logout_student(authorization: str = Header(), student=Depends(get_current_student)):
    token = authorization.removeprefix("Bearer ").strip()
    database.delete_session(hashlib.sha256(token.encode("utf-8")).hexdigest())
    return {"message": "Signed out successfully."}


@app.post("/api/admin/login")
def login_admin(request: AdminLoginRequest):
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        raise HTTPException(
            status_code=503,
            detail="Admin login is not configured. Set ADMIN_EMAIL and ADMIN_PASSWORD in backend/.env.",
        )

    email_matches = hmac.compare_digest(
        request.email.encode("utf-8"),
        ADMIN_EMAIL.encode("utf-8"),
    )
    password_matches = hmac.compare_digest(
        request.password.encode("utf-8"),
        ADMIN_PASSWORD.encode("utf-8"),
    )
    if not (email_matches and password_matches):
        raise HTTPException(status_code=401, detail="Admin email or password is incorrect.")

    return {
        "access_token": create_admin_access_token(),
        "token_type": "bearer",
    }


@app.post("/api/admin/logout")
def logout_admin(
    authorization: str = Header(),
    admin=Depends(get_current_admin),
):
    token = authorization.removeprefix("Bearer ").strip()
    database.delete_admin_session(hashlib.sha256(token.encode("utf-8")).hexdigest())
    return {"message": "Admin signed out successfully."}


@app.get("/api/admin/syllabi")
def list_admin_syllabi(admin=Depends(get_current_admin)):
    return database.list_syllabi()


@app.post("/api/admin/syllabi", status_code=201)
def create_or_update_syllabus(
    request: SyllabusEntryRequest,
    admin=Depends(get_current_admin),
):
    return save_syllabus(request, request.syllabus_text)


@app.post("/api/admin/syllabi/upload", status_code=201)
async def upload_syllabus_pdf(
    department: str = Form(..., min_length=2, max_length=80),
    year: int = Form(..., ge=1, le=4),
    semester: int = Form(..., ge=1, le=8),
    subject_code: str = Form(..., min_length=1, max_length=30),
    subject_name: str = Form(..., min_length=2, max_length=120),
    important_questions: str = Form(..., min_length=1, max_length=100_000),
    pdf: UploadFile = File(...),
    admin=Depends(get_current_admin),
):
    contents = await pdf.read(MAX_PDF_BYTES + 1)
    await pdf.close()
    if len(contents) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="PDF must be 10 MB or smaller.")
    if not contents.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail="Upload a valid PDF file.")

    try:
        reader = PdfReader(io.BytesIO(contents))
        if reader.is_encrypted:
            raise HTTPException(status_code=422, detail="Password-protected PDFs are not supported.")
        syllabus_text = "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except (PdfReadError, OSError, ValueError) as error:
        logger.warning("Could not extract syllabus PDF text: %s", error)
        raise HTTPException(status_code=422, detail="Could not read this PDF. Try another PDF.") from error

    if not syllabus_text:
        raise HTTPException(
            status_code=422,
            detail="This PDF has no selectable text. Upload a text-based PDF or paste the syllabus.",
        )
    if len(syllabus_text) > MAX_SYLLABUS_CHARACTERS:
        raise HTTPException(status_code=413, detail="Extracted syllabus is too large to save.")

    try:
        fields = SyllabusFields(
            department=department,
            year=year,
            semester=semester,
            subject_code=subject_code,
            subject_name=subject_name,
            important_questions=important_questions,
        )
    except ValidationError as error:
        raise HTTPException(
            status_code=422,
            detail=error.errors(include_context=False, include_url=False),
        ) from error

    source_filename = Path(pdf.filename or "syllabus.pdf").name
    return save_syllabus(fields, syllabus_text, source_filename)


@app.delete("/api/admin/syllabi/{syllabus_id}")
def remove_syllabus(syllabus_id: int, admin=Depends(get_current_admin)):
    if not database.delete_syllabus(syllabus_id):
        raise HTTPException(status_code=404, detail="Syllabus entry not found.")
    return {"message": "Syllabus entry deleted."}


@app.get("/api/syllabus")
def read_student_syllabus(student=Depends(get_current_student)):
    if not student["department"] or not student["study_year"] or not student["semester"]:
        return []
    return database.list_syllabi(
        student["department"],
        student["study_year"],
        student["semester"],
    )


@app.post("/api/courses", status_code=201)
def create_course(request: CourseRequest, student=Depends(get_current_student)):
    try:
        return database.create_course(
            student["id"],
            request.title,
            request.category,
            request.total_lessons,
            int(time.time()),
        )
    except sqlite3.IntegrityError as error:
        if "UNIQUE constraint failed" not in str(error):
            raise
        raise HTTPException(status_code=409, detail="You already have a course with this title.")


@app.patch("/api/courses/{course_id}/progress")
def update_course_progress(
    course_id: int,
    request: CourseProgressRequest,
    student=Depends(get_current_student),
):
    course = database.update_course_progress(
        student["id"],
        course_id,
        request.lessons_completed,
    )
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found.")
    if course is False:
        raise HTTPException(status_code=422, detail="Completed lessons cannot exceed the course total.")
    return course


@app.get("/api/dashboard")
def read_dashboard(current_student=Depends(get_current_student)):
    courses = database.list_courses(current_student["id"])
    syllabi = (
        database.list_syllabi(
            current_student["department"],
            current_student["study_year"],
            current_student["semester"],
        )
        if current_student["department"]
        and current_student["study_year"]
        and current_student["semester"]
        else []
    )
    lessons_completed = sum(course["lessonsCompleted"] for course in courses)
    total_lessons = sum(course["totalLessons"] for course in courses)
    return {
        "student": public_student(current_student),
        "stats": [
            {"id": "streak", "label": "Study streak", "value": "12 days", "change": "+2 days"},
            {"id": "hours", "label": "Study time this week", "value": "18.5 hrs", "change": "+16%"},
            {
                "id": "lessons",
                "label": "Lessons completed",
                "value": f"{lessons_completed} / {total_lessons}",
                "change": f"{len(courses)} courses",
            },
            {"id": "focus", "label": "Average focus score", "value": "92%", "change": "+5%"},
        ],
        "weeklyActivity": [
            {"day": "Mon", "studyHours": 2.3, "focusHours": 1.8},
            {"day": "Tue", "studyHours": 3.2, "focusHours": 2.5},
            {"day": "Wed", "studyHours": 2.7, "focusHours": 2.2},
            {"day": "Thu", "studyHours": 4.1, "focusHours": 3.3},
            {"day": "Fri", "studyHours": 2.9, "focusHours": 2.4},
            {"day": "Sat", "studyHours": 3.6, "focusHours": 3.0},
            {"day": "Sun", "studyHours": 2.4, "focusHours": 1.9},
        ],
        "weeklyGoal": {
            "completed": 18.5,
            "hours": 24,
            "percent": 77,
            "remaining": 5.5,
        },
        "courses": courses,
        "syllabi": syllabi,
        "schedule": [
            {
                "id": "session-1",
                "time": "09:30 AM",
                "subject": "Machine Learning",
                "detail": "Module 04 · Neural Networks",
                "type": "Lecture",
                "color": "violet",
            },
            {
                "id": "session-2",
                "time": "11:00 AM",
                "subject": "Data Visualization",
                "detail": "Practice · Build a dashboard",
                "type": "Practice",
                "color": "mint",
            },
            {
                "id": "session-3",
                "time": "02:30 PM",
                "subject": "Daily reflection",
                "detail": "Review today's learning",
                "type": "Completed",
                "color": "coral",
            },
        ],
    }
