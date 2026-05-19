# UniVerse — Backend API

## Overview

UniVerse is the server-side application powering a university management platform. It exposes a REST API that handles authentication, student and faculty profiles, attendance tracking, campus events, announcements, a social feed, timetable management, and an in-app notification system. The backend is designed to work alongside a separately maintained frontend/mobile application.

The system connects to three distinct PostgreSQL databases: a primary mutable database for application data, a read-only master student registry for registration validation, and a read-only faculty registry for identity lookups.

---

## Features

- JWT-based authentication with access and refresh token support
- OTP (one-time password) generation and email delivery for login and password reset
- Google OAuth login via ID token verification
- Role-based access control across three roles: student, faculty, and admin
- Student registration validated against a master student registry database
- Faculty login provisioned from a separate faculty database
- Subject-wise and day-wise attendance tracking marked by faculty per timetable slot
- Duplicate submission prevention with unique constraints per student, subject, date, and time slot
- Student attendance overview, daily breakdown, and summary with overall percentage
- Attendance record export per student (by registration number)
- Campus announcements categorised as exam, result, holiday, or general notices
- Campus event management with image upload, registration slots, and student registration tracking
- Social feed with posts, likes, and comments including admin soft-delete
- Class timetable management for students and faculty
- In-app notification system with read/unread tracking
- Avatar upload and static file serving for user profile images
- Health check endpoint with live database connectivity verification
- Global exception handling with structured JSON error responses

---

## Tech Stack

**Backend Framework**
- FastAPI 0.111.0
- Uvicorn 0.29.0 (ASGI server)
- Python 3.10

**Database**
- PostgreSQL (three separate instances: student app DB, master student registry, faculty registry)
- SQLAlchemy 2.0.30 (ORM)
- Alembic 1.13.1 (schema migrations)
- psycopg2-binary 2.9.9 (PostgreSQL adapter)

**Authentication**
- python-jose 3.3.0 (JWT encoding and decoding)
- passlib 1.7.4 with bcrypt 4.0.1 (password hashing)
- google-auth 2.29.0 (Google ID token verification)

**Email**
- fastapi-mail / smtplib (SMTP email for OTP delivery)

**File Handling**
- Pillow 10.3.0 (image processing)
- aiofiles 23.2.1 (async file I/O)
- python-multipart 0.0.9 (multipart form data for file uploads)

**Validation and Settings**
- Pydantic 2.7.1 with email support
- pydantic-settings 2.2.1
- python-dotenv 1.0.1

**Deployment**
- Render (cloud platform)
- Live API: `https://universe-mainbackend.onrender.com`

---

## Project Structure

```
Universe-MainBackend/
├── app/
│   ├── main.py                  # FastAPI app factory, middleware, router registration
│   ├── config.py                # Pydantic settings (env vars, defaults)
│   ├── database.py              # SQLAlchemy engines and session factories for all three DBs
│   ├── auth/
│   │   ├── utils.py             # Password hashing, JWT creation/decoding, OTP, Google token verification
│   │   └── dependencies.py      # FastAPI dependency injectors: get_current_user, require_roles, etc.
│   ├── models/
│   │   ├── __init__.py          # All ORM models: User, Announcement, Event, Attendance, Post, etc.
│   │   ├── faculty.py           # Faculty model mapped to faculty DB
│   │   └── student.py           # MasterStudent model mapped to master registry DB
│   ├── routers/
│   │   ├── auth.py              # /auth — register, login, refresh, OTP, Google login, password reset
│   │   ├── users.py             # /users — profile management, avatar upload
│   │   ├── students.py          # /students — admin student management
│   │   ├── announcements.py     # /announcements — CRUD for notices
│   │   ├── events.py            # /events — campus events and student registrations
│   │   ├── attendance_v2.py     # /attendance — attendance marking, queries, export (active version)
│   │   ├── attendance.py        # /attendance v1 (superseded, not registered)
│   │   ├── timetable.py         # /timetable — student class schedules
│   │   ├── faculty_timetable.py # /timetable/faculty — faculty schedule views
│   │   ├── posts.py             # /posts — social feed posts, likes, comments
│   │   ├── notifications.py     # /notifications — in-app notification management
│   │   └── jobs.py              # /jobs — placement listings (defined but not registered in Swagger)
│   ├── schemas/
│   │   └── __init__.py          # All Pydantic request/response schemas
│   └── utils/
│       ├── email_service.py     # Email utility wrapper
│       ├── files.py             # File upload helpers
│       └── pagination.py        # Pagination utilities
├── alembic/
│   ├── env.py                   # Alembic migration environment
│   └── versions/
│       └── 001_add_time_slot.py # Migration: adds time_slot and section to day_attendance
├── alembic.ini                  # Alembic configuration
├── requirements.txt
├── runtime.txt                  # Python 3.10.0
├── faculty_schema.sql           # Faculty DB schema
├── faculty_data.sql             # Faculty seed data
├── students_schema.sql          # Master student registry schema
├── students_data.sql            # Student seed data
├── universe_data.sql            # Main app DB seed data
├── fix_plaintext_passwords.py   # Utility: re-hash plain-text passwords stored in DB
├── recovery_script.py           # Recovery utility
└── uploads/
    └── avatars/                 # Stored user avatar images
```

---

## System Architecture

The application connects to three separate PostgreSQL databases at startup:

- **Student DB** (`STUDENT_DATABASE_URL`): The primary read-write database. All application models — users, posts, events, attendance, timetable, notifications — are stored here.
- **Master DB** (`MASTER_DATABASE_URL`): A read-only student registry. During registration, the backend validates that a student's email exists in this database and copies their name, department, year, section, and registration number into the student DB.
- **Faculty DB** (`FACULTY_DATABASE_URL`): A read-only faculty directory. Faculty login resolves identity from this database, then provisions or retrieves a corresponding user record in the student DB.

At startup, the application validates that all three database URLs are present, verifies that default password environment variables are plain text (not pre-hashed), creates upload directories, and runs `Base.metadata.create_all` to ensure tables exist.

Connection pooling is configured with `pool_pre_ping`, a 10-minute `pool_recycle`, SSL required, and a 10-second connection timeout.

---

## API Architecture

Routers are registered on the FastAPI app with the following prefixes:

| Prefix               | Router file              | Responsibility                                   |
|----------------------|--------------------------|--------------------------------------------------|
| `/auth`              | `auth.py`                | Authentication, OTP, Google login, password reset|
| `/users`             | `users.py`               | User profiles, avatar management                 |
| `/students`          | `students.py`            | Admin-level student CRUD                         |
| `/announcements`     | `announcements.py`       | Notices for students and admin                   |
| `/events`            | `events.py`              | Campus events and registration                   |
| `/attendance`        | `attendance_v2.py`       | Attendance marking and student queries (v2 only) |
| `/timetable`         | `timetable.py`           | Student class schedules                          |
| `/timetable/faculty` | `faculty_timetable.py`   | Faculty schedule views                           |
| `/posts`             | `posts.py`               | Social feed                                      |
| `/notifications`     | `notifications.py`       | In-app notifications                             |

Each router uses FastAPI dependency injection to resolve the current authenticated user and enforce role requirements. The three database sessions are injected as separate dependencies (`get_student_db`, `get_master_db`, `get_faculty_db`).

Static files (uploaded avatars and event images) are served directly via FastAPI's `StaticFiles` mount at `/uploads`.

---

## Installation and Setup

**Prerequisites:** Python 3.10, PostgreSQL

```bash
# Clone the repository
git clone <repository-url>
cd Universe-MainBackend

# Create and activate a virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy environment file and fill in values
cp .env.example .env

# Run database migrations
alembic upgrade head

# Start the development server
uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000`.  
Interactive documentation is at `http://localhost:8000/docs`.

---

## Environment Variables

Create a `.env` file in the project root with the following variables:

```env
# Database connections
STUDENT_DATABASE_URL=postgresql://user:password@host:5432/student_db
MASTER_DATABASE_URL=postgresql://user:password@host:5432/master_db
FACULTY_DATABASE_URL=postgresql://user:password@host:5432/faculty_db

# JWT
SECRET_KEY=your-secret-key-minimum-32-characters
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60
REFRESH_TOKEN_EXPIRE_DAYS=7

# Default passwords (plain text — do NOT store pre-hashed values here)
DEFAULT_PASSWORD=Uni123
FACULTY_DEFAULT_PASSWORD=faculty@123

# OTP
OTP_EXPIRY_MINUTES=5

# Google OAuth
GOOGLE_CLIENT_ID=your-google-client-id

# SMTP email
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_FROM_EMAIL=your-email@gmail.com

# App
APP_NAME=UniVerse
APP_VERSION=1.0.0
DEBUG=false
BASE_URL=https://universe-mainbackend.onrender.com

# CORS (comma-separated origins)
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:5173

# File uploads
UPLOAD_DIR=uploads
MAX_FILE_SIZE_MB=5
```

---

## Database

The application uses three PostgreSQL databases managed through SQLAlchemy ORM.

**Student DB (read-write):** Stores all mutable application data.

Key models:

| Model               | Table                | Description                                                   |
|---------------------|----------------------|---------------------------------------------------------------|
| `User`              | `users`              | Students, faculty, and admins with role, profile, OTP fields  |
| `Announcement`      | `announcements`      | Exam, result, holiday, and general notices                    |
| `Event`             | `events`             | Campus events with slot tracking                              |
| `SubjectAttendance` | `subject_attendance` | Running attendance totals per student per subject             |
| `DayAttendance`     | `day_attendance`     | Individual attendance records per class period                |
| `Attendance`        | `attendance`         | Faculty submission snapshots per student per time slot        |
| `Post`              | `posts`              | Social feed posts with soft-delete support                    |
| `Comment`           | `comments`           | Comments on posts                                             |
| `Timetable`         | `timetable`          | Class schedule entries linked to faculty                      |
| `Notification`      | `notifications`      | In-app notifications per user                                 |
| `Job`               | `jobs`               | Placement listings (model defined; router not publicly exposed)|

**Master DB (read-only):** Contains the `students` table with official student records. Used during registration to validate and import student identity data.

**Faculty DB (read-only):** Contains the `faculty` table. Used during faculty login to locate and provision faculty accounts.

Alembic manages schema migrations for the student DB. The master and faculty databases are external read-only sources and are never altered by Alembic.

---

## Authentication

The application implements a multi-method authentication system:

**Credential Login**
1. The client sends an email and password to `POST /auth/login`.
2. For students, the backend resolves the user from the student DB and verifies the bcrypt password hash.
3. For faculty, the backend looks up the email in the faculty DB and verifies against the configured faculty default password (or any updated password stored in the student DB user record).
4. On success, an access token (JWT, configurable expiry, default 60 minutes) and a refresh token (JWT, default 7 days) are returned.

**Token Refresh**
- `POST /auth/refresh` accepts a valid refresh token and issues a new access token.

**OTP Login / Password Reset**
1. `POST /auth/send-otp` generates a 6-digit OTP, stores it against the user with an expiry timestamp, and sends it by email via SMTP.
2. `POST /auth/verify-otp` validates the OTP and issues tokens on success.
3. `POST /auth/forgot-password` and `POST /auth/reset-password` use the same OTP flow to allow password changes without a current password.

**Google OAuth**
- `POST /auth/google-login` accepts a Google ID token, verifies it against the configured `GOOGLE_CLIENT_ID` using `google-auth`, and either logs in or provisions a new user account.

**First Login**
- New student accounts are created with `is_first_login=True` and the default password from `DEFAULT_PASSWORD`. Clients are expected to prompt the user to change their password on first access.

**Role-Based Access**
- Three roles are enforced: `student`, `faculty`, and `admin`.
- FastAPI dependencies (`require_admin`, `require_faculty_or_admin`, `require_student`, `require_roles`) are injected at the route level to restrict access.

---

## API Endpoints

### Authentication — `/auth`

| Method | Path                  | Description                              | Auth Required |
|--------|-----------------------|------------------------------------------|---------------|
| POST   | `/auth/register`      | Register a new student account           | No            |
| POST   | `/auth/login`         | Login with email and password            | No            |
| POST   | `/auth/refresh`       | Refresh access token                     | No            |
| POST   | `/auth/logout`        | Logout (client-side token discard)       | Yes           |
| GET    | `/auth/me`            | Get authenticated user profile           | Yes           |
| POST   | `/auth/change-password` | Change password                        | Yes           |
| POST   | `/auth/send-otp`      | Send OTP to registered email             | No            |
| POST   | `/auth/verify-otp`    | Verify OTP and receive tokens            | No            |
| POST   | `/auth/forgot-password` | Request password reset OTP             | No            |
| POST   | `/auth/reset-password`| Reset password with OTP                  | No            |
| POST   | `/auth/google-login`  | Login or register via Google ID token    | No            |

### Users — `/users`

| Method | Path                  | Description                              | Auth Required |
|--------|-----------------------|------------------------------------------|---------------|
| GET    | `/users/me`           | Get own profile                          | Yes           |
| PATCH  | `/users/me`           | Update own profile                       | Yes           |
| POST   | `/users/upload-avatar`| Upload profile avatar image              | Yes           |
| DELETE | `/users/avatar`       | Remove profile avatar                    | Yes           |
| GET    | `/users`              | List all users (admin)                   | Yes (admin)   |
| GET    | `/users/{user_id}`    | Get user by ID                           | Yes           |
| PATCH  | `/users/{user_id}`    | Update user by ID (admin)                | Yes (admin)   |
| DELETE | `/users/{user_id}`    | Delete user by ID (admin)                | Yes (admin)   |

### Announcements — `/announcements`

| Method | Path                          | Description                      | Auth Required      |
|--------|-------------------------------|----------------------------------|--------------------|
| GET    | `/announcements`              | List announcements               | Yes                |
| POST   | `/announcements`              | Create announcement              | Yes (admin)        |
| GET    | `/announcements/{id}`         | Get announcement by ID           | Yes                |
| DELETE | `/announcements/{id}`         | Delete announcement              | Yes (admin)        |

### Events — `/events`

| Method | Path                          | Description                      | Auth Required      |
|--------|-------------------------------|----------------------------------|--------------------|
| GET    | `/events`                     | List all events                  | Yes                |
| POST   | `/events`                     | Create event                     | Yes (admin/faculty)|
| GET    | `/events/{id}`                | Get event details                | Yes                |
| POST   | `/events/{id}/image`          | Upload event image               | Yes (admin/faculty)|
| DELETE | `/events/{id}`                | Delete event                     | Yes (admin/faculty)|
| POST   | `/events/{id}/register`       | Register for an event            | Yes (student)      |
| DELETE | `/events/{id}/register`       | Cancel event registration        | Yes (student)      |
| GET    | `/events/my-registrations`    | Get student's registered events  | Yes (student)      |

### Attendance — `/attendance`

| Method | Path                           | Description                                      | Auth Required         |
|--------|--------------------------------|--------------------------------------------------|-----------------------|
| POST   | `/attendance/mark`             | Mark attendance for a session                    | Yes (faculty/admin)   |
| POST   | `/attendance/unlock`           | Unlock a previously submitted attendance session | Yes (faculty/admin)   |
| GET    | `/attendance/faculty/schedule` | Get faculty's timetable slots                    | Yes (faculty/admin)   |
| GET    | `/attendance/faculty/students` | Get student list for a section                   | Yes (faculty/admin)   |
| GET    | `/attendance/check`            | Check if attendance is already marked for a slot | Yes (faculty/admin)   |
| GET    | `/attendance/me`               | Student's subject-wise attendance                | Yes (student)         |
| GET    | `/attendance/me/overview`      | Student's full attendance overview               | Yes (student)         |
| GET    | `/attendance/me/daily`         | Student's day-wise attendance records            | Yes (student)         |
| GET    | `/attendance/me/summary`       | Student's summary with overall percentage        | Yes (student)         |
| GET    | `/attendance/student/{id}`     | Any student's attendance (by ID or reg number)   | Yes (faculty/admin)   |
| GET    | `/attendance/admin/overall`    | Overall attendance view for admin                | Yes (admin)           |
| GET    | `/attendance/download/{regnum}`| Download attendance report for a student         | Yes (faculty/admin)   |

### Posts (Social Feed) — `/posts`

| Method | Path              | Description                    | Auth Required |
|--------|-------------------|--------------------------------|---------------|
| GET    | `/posts`          | List posts (feed)              | Yes           |
| POST   | `/posts`          | Create a post                  | Yes           |
| GET    | `/posts/{id}`     | Get post by ID                 | Yes           |
| DELETE | `/posts/{id}`     | Delete own post or admin delete| Yes           |

### Timetable — `/timetable`

| Method | Path                  | Description                  | Auth Required    |
|--------|-----------------------|------------------------------|------------------|
| GET    | `/timetable`          | Get student timetable        | Yes              |
| POST   | `/timetable`          | Create timetable entry       | Yes (admin)      |
| PUT    | `/timetable/{id}`     | Update timetable entry       | Yes (admin)      |
| DELETE | `/timetable/{id}`     | Delete timetable entry       | Yes (admin)      |
| GET    | `/timetable/faculty`  | Get faculty schedule         | Yes (faculty)    |

### Notifications — `/notifications`

| Method | Path                              | Description                   | Auth Required |
|--------|-----------------------------------|-------------------------------|---------------|
| GET    | `/notifications`                  | Get user notifications        | Yes           |
| GET    | `/notifications/unread-count`     | Get unread notification count | Yes           |
| POST   | `/notifications/{id}/read`        | Mark notification as read     | Yes           |
| POST   | `/notifications/read-all`         | Mark all notifications read   | Yes           |
| POST   | `/notifications/send`             | Send a notification (admin)   | Yes (admin)   |

### Health

| Method | Path      | Description                                    |
|--------|-----------|------------------------------------------------|
| GET    | `/`       | App name, version, and status                  |
| GET    | `/health` | Live database connectivity check               |

---

## Deployment

The backend is deployed on [Render](https://render.com) and is publicly accessible at:

```
https://universe-mainbackend.onrender.com
```

Interactive API documentation is available at:

```
https://universe-mainbackend.onrender.com/docs
```

The Python runtime version is specified in `runtime.txt` as `python-3.10.0`. Environment variables are configured through the Render dashboard under the service's Environment settings. The application validates all required database URLs and password variables at startup and terminates with a descriptive error if any are missing or malformed.

---

## Related Repository

This repository contains the backend REST API and all server-side services for the UniVerse platform.

The frontend/mobile application is maintained in a separate repository and communicates with this backend exclusively through the HTTP API described above.

---

## Future Enhancements

- Attendance unlock and correction workflow with audit logging
- Push notification delivery via FCM or APNs for mobile clients
- Placement/jobs module exposed publicly with student application tracking
- Granular permission system beyond the current three-role model
- Rate limiting on OTP and authentication endpoints
- Automated test suite covering critical authentication and attendance flows
- API versioning strategy for non-breaking evolution of existing endpoints

---

## Contributors

This project was developed as a final year project.

- Sai Moksha Naimisha Namburu
- Sadasivuni Gyaneswari
- Salapu Karthik
- Senapathi Sai Venkat Rahul

Department of Computer Science and Systems Engineering Andhra University College of Engineering (A)

---

## License

This project is currently unlicensed. All rights are reserved by the project contributors unless otherwise specified.
