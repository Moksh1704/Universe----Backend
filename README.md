# UNIVERSE – Campus Social Mobile Application (Backend)

UNIVERSE is a modern campus social and academic management platform designed to connect students, faculty, and administrators through a unified digital ecosystem.

This repository contains the **FastAPI backend** for the UNIVERSE application, powering authentication, attendance management, events, notifications, social feeds, timetable management, and campus services.

The project was developed as part of a Bachelor of Technology major project under the Department of Computer Science and Systems Engineering, Andhra University.

---

## Project Overview

Educational institutions often rely on multiple disconnected platforms for announcements, communication, attendance tracking, and academic updates. UNIVERSE solves this problem by providing a centralized and institution-specific platform for campus management and social interaction.

The backend is built using:

- **FastAPI** – REST API framework
- **PostgreSQL** – Relational database
- **JWT Authentication** – Secure session handling
- **Bcrypt** – Password hashing
- **Google OAuth / Firebase Authentication** – Secure login support
- **SQLAlchemy** – ORM support
- **Alembic** – Database migrations

The system architecture follows a layered client-server model using React Native, FastAPI, and PostgreSQL.

---

## Features

### Authentication & Authorization

- JWT-based authentication
- Secure password hashing using Bcrypt
- Role-based access control
- Google Authentication support
- Student, Faculty, and Admin roles

### Campus Feed & Announcements

- Create and manage announcements
- Social feed functionality
- Posts and interactions
- Notifications system

### Event Management

- Create and manage events
- Event registration support
- Event updates and tracking

### Academic Utilities

- Attendance tracking
- Subject-wise attendance management
- Faculty attendance management
- Timetable management

### Communication Features

- Chat feed system
- User notifications
- Real-time style interaction using APIs

### Additional Functionalities

- File upload support
- Pagination utilities
- Role-based protected routes
- API documentation with Swagger UI

---

## Tech Stack

| Category               | Technology                        |
| ---------------------- | --------------------------------- |
| Backend Framework      | FastAPI                           |
| Database               | PostgreSQL                        |
| ORM                    | SQLAlchemy                        |
| Authentication         | JWT + Bcrypt                      |
| API Testing            | Postman                           |
| Migration Tool         | Alembic                           |
| Environment Management | Python-dotenv / Pydantic Settings |
| Version Control        | Git & GitHub                      |

---

## Project Structure

```bash
Universe-----MainBackend-main/
│
├── app/
│   ├── auth/
│   │   ├── dependencies.py
│   │   └── utils.py
│   │
│   ├── models/
│   │   ├── faculty.py
│   │   └── student.py
│   │
│   ├── routers/
│   │   ├── announcements.py
│   │   ├── attendance.py
│   │   ├── attendance_v2.py
│   │   ├── auth.py
│   │   ├── events.py
│   │   ├── faculty_timetable.py
│   │   ├── jobs.py
│   │   ├── notifications.py
│   │   ├── posts.py
│   │   ├── students.py
│   │   ├── timetable.py
│   │   └── users.py
│   │
│   ├── utils/
│   │   ├── email_service.py
│   │   ├── files.py
│   │   └── pagination.py
│   │
│   ├── config.py
│   ├── database.py
│   └── main.py
│
├── alembic/
├── uploads/
├── requirements.txt
├── alembic.ini
└── README.md
```

---

## Getting Started

### Clone the Repository

```bash
git clone https://github.com/your-username/universe-backend.git
cd universe-backend
```

### Create a Virtual Environment

**Windows**

```bash
python -m venv venv
venv\Scripts\activate
```

**Linux / Mac**

```bash
python3 -m venv venv
source venv/bin/activate
```

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Environment Variables

Create a `.env` file in the root directory and configure the following:

```env
DATABASE_URL=postgresql://username:password@localhost:5432/universe_db
SECRET_KEY=your_secret_key
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60
```

### Database Setup

Create a PostgreSQL database:

```sql
CREATE DATABASE universe_db;
```

Run migrations:

```bash
alembic upgrade head
```

### Run the Application

```bash
uvicorn app.main:app --reload
```

Server will run at `http://127.0.0.1:8000`

---

## Live Deployment

The backend API is deployed on Render:

```
https://universe-mainbackend.onrender.com
```

Live API docs: `https://universe-mainbackend.onrender.com/docs`

---

## API Documentation

FastAPI automatically generates API documentation.

| Interface  | URL                           |
| ---------- | ----------------------------- |
| Swagger UI | `http://127.0.0.1:8000/docs`  |
| ReDoc      | `http://127.0.0.1:8000/redoc` |

---

## Main API Modules

| Module           | Description                    |
| ---------------- | ------------------------------ |
| `/auth`          | Authentication & JWT handling  |
| `/users`         | User profile management        |
| `/attendance`    | Attendance management          |
| `/events`        | Event management               |
| `/posts`         | Campus social feed             |
| `/notifications` | Notifications system           |
| `/timetable`     | Timetable management           |
| `/announcements` | Campus announcements           |
| `/jobs`          | Placement/job related features |

---

## Security Features

- JWT Token Authentication
- Password Encryption using Bcrypt
- Protected API Routes
- Role-Based Authorization
- Secure REST API Architecture

---

## System Architecture

```text
Mobile App / Admin Panel
        ↓
   FastAPI Backend
        ↓
  PostgreSQL Database
```

The backend handles authentication, API processing, business logic, database operations, and secure communication.

---

## Key Objectives

- Centralized campus communication
- Integrated academic utilities
- Secure institutional access
- Attendance and timetable management
- Campus event handling
- Social interaction platform for students
- Improved campus engagement

---

## Future Enhancements

- Real-time WebSocket chat
- Push notifications
- AI-powered campus assistant
- Multi-university support
- Advanced analytics dashboard
- Cloud deployment
- Enhanced moderation tools

---

## Developed By

- Sai Moksha Naimisha Namburu
- Sadasivuni Gyaneswari
- Salapu Karthik
- Senapathi Sai Venkat Rahul

Department of Computer Science and Systems Engineering
Andhra University College of Engineering (A)

---

## License

This project is developed for academic and educational purposes.

---

## Acknowledgement

Special thanks to the Department of Computer Science and Systems Engineering, Andhra University, for supporting the development of this project.
