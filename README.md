# Admin syllabus setup

## Set admin credentials

Add these lines to `backend/.env`. If the file already exists, append them without replacing other settings:

```env
ADMIN_EMAIL=your-admin-email@example.com
ADMIN_PASSWORD=choose-a-long-private-password
```

Keep `.env` private; it is ignored by Git. Restart the backend after saving:

```powershell
python -m uvicorn main:app --reload
```

Open `http://localhost:5173/admin` and sign in with those credentials. Add one subject per entry, choose its department/year/semester, then paste syllabus text or upload a selectable-text PDF (up to 10 MB). Enter admin-approved practice questions one per line. Scanned/image-only PDFs are not supported.

Students choose their department/year/semester during registration and see only matching entries. Existing student accounts can complete those details after signing in.
