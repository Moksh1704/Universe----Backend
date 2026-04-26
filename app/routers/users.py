from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db, get_master_db, get_faculty_db
from app.models import User, UserRole, MasterStudent
from app.models.faculty import FacultyMember
from app.schemas import UserProfileResponse, UpdateProfileRequest, AdminUpdateUserRequest, MessageResponse
from app.auth.dependencies import get_current_user, require_admin
from app.utils.files import save_upload_file

router = APIRouter(prefix="/users", tags=["Users"])


# ✅ PROFILE API — supports faculty, student, and admin roles
@router.get("/me")
def get_my_profile(
    current_user: User = Depends(get_current_user),
    master_db: Session = Depends(get_master_db),
    faculty_db: Session = Depends(get_faculty_db),
):
    """
    Get current user profile. Response shape varies by role:

    - faculty → profile from faculty_db (FacultyMember table)
    - student → profile from master_db  (MasterStudent table)
    - admin   → lightweight profile from auth User record
    """
    role  = current_user.role
    email = current_user.email

    # ── Faculty ──────────────────────────────────────────────────────────────
    if role == "faculty":
        faculty = faculty_db.query(FacultyMember).filter(
            func.lower(FacultyMember.email) == email.lower()
        ).first()

        if not faculty:
            raise HTTPException(
                status_code=404,
                detail="Faculty profile not found in faculty database",
            )

        return {
            "id":          faculty.id,
            "name":        faculty.name,
            "email":       faculty.email,
            "department":  getattr(faculty, "department",  None),
            "designation": getattr(faculty, "designation", None),
            "avatarUrl":   current_user.avatar_url,
            "role":        "faculty",
            "isActive":    current_user.is_active,
            "createdAt":   current_user.created_at,
        }

    # ── Student ───────────────────────────────────────────────────────────────
    if role == "student":
        student = master_db.query(MasterStudent).filter(
            func.lower(MasterStudent.email) == email.lower()
        ).first()

        if not student:
            raise HTTPException(
                status_code=404,
                detail="Student profile not found in master database",
            )

        return {
            "id":                 current_user.id,
            "name":               student.name or current_user.name,
            "fullname":           student.fullname,
            "email":              student.email,
            "department":         student.department,
            "course":             student.course,
            "year":               student.year,
            "section":            student.section,
            "registrationNumber": student.regnum,
            "cgpa":               student.cgpa,
            "avatarUrl":          current_user.avatar_url,
            "role":               "student",
            "isActive":           current_user.is_active,
            "createdAt":          current_user.created_at,
        }

    # ── Admin (and any future roles) ──────────────────────────────────────────
    return {
        "id":        current_user.id,
        "name":      current_user.name,
        "email":     email,
        "avatarUrl": current_user.avatar_url,
        "role":      role,
        "isActive":  current_user.is_active,
        "createdAt": current_user.created_at,
    }


# ─────────────────────────────────────────────

@router.patch("/me", response_model=UserProfileResponse)
def update_my_profile(
    payload: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update own profile (user DB only)."""

    if payload.name is not None:
        current_user.name = payload.name
    if payload.department is not None:
        current_user.department = payload.department
    if payload.year is not None:
        current_user.year = payload.year
    if payload.section is not None:
        current_user.section = payload.section
    if payload.designation is not None:
        current_user.designation = payload.designation

    db.commit()
    db.refresh(current_user)
    return UserProfileResponse.model_validate(current_user)


# ─────────────────────────────────────────────

@router.post("/me/avatar", response_model=UserProfileResponse)
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload profile avatar."""

    url = await save_upload_file(file, folder="avatars")
    current_user.avatar_url = url

    db.commit()
    db.refresh(current_user)

    return UserProfileResponse.model_validate(current_user)


# ─────────────────────────────────────────────

# DEV ROUTE — no auth, internal/admin website use only
# Declared before /{user_id} to avoid route shadowing.

@router.get("/faculty-dev", response_model=List[dict])
def get_all_faculty_dev():
    """Dev endpoint — returns static faculty list with integer faculty_id for timetable filtering."""

    return [
        {"faculty_id": 6,  "name": "Faculty 6"},
        {"faculty_id": 8,  "name": "Faculty 8"},
        {"faculty_id": 10, "name": "Faculty 10"},
    ]


# ─────────────────────────────────────────────

@router.get("", response_model=List[UserProfileResponse])
def get_all_users(
    role: Optional[UserRole] = Query(None),
    department: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin: Get all users."""

    query = db.query(User)

    if role:
        query = query.filter(User.role == role)
    if department:
        query = query.filter(User.department.ilike(f"%{department}%"))
    if search:
        query = query.filter(
            (User.name.ilike(f"%{search}%")) |
            (User.email.ilike(f"%{search}%"))
        )

    users = query.offset((page - 1) * pageSize).limit(pageSize).all()
    return [UserProfileResponse.model_validate(u) for u in users]


# ─────────────────────────────────────────────

@router.get("/{user_id}", response_model=UserProfileResponse)
def get_user(
    user_id: str,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin: Get specific user."""

    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return UserProfileResponse.model_validate(user)


# ─────────────────────────────────────────────

@router.patch("/{user_id}", response_model=UserProfileResponse)
def admin_update_user(
    user_id: str,
    payload: AdminUpdateUserRequest,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin: Update user."""

    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    for field, value in payload.model_dump(exclude_none=True).items():
        snake = {"isActive": "is_active"}.get(field, field)
        setattr(user, snake, value)

    db.commit()
    db.refresh(user)

    return UserProfileResponse.model_validate(user)


# ─────────────────────────────────────────────

@router.delete("/{user_id}", response_model=MessageResponse)
def delete_user(
    user_id: str,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin: Delete user."""

    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    db.delete(user)
    db.commit()

    return MessageResponse(message="User deleted successfully")