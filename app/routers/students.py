"""
app/routers/students.py

GET    /students       — list users from `users` table (filterable by role, section, year, department)
POST   /students       — create a new user
PUT    /students/{id}  — update an existing user
DELETE /students/{id}  — delete a user

Uses ONLY the User model. No MasterStudent references anywhere.
Roll number is sourced from User.registration_number and exposed as "roll_no".
Passwords and OTP fields are never returned.
"""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.database import get_student_db
from app.models import User, UserRole

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/students", tags=["Students"])


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    name:         str            = Field(..., min_length=1, max_length=200)
    email:        EmailStr
    role:         UserRole       = UserRole.student
    nickname:     Optional[str]  = Field(None, max_length=200)
    avatar_url:   Optional[str]  = Field(None, max_length=500)
    department:   Optional[str]  = Field(None, max_length=200)
    year:         Optional[int]  = Field(None, ge=1, le=6)
    section:      Optional[str]  = Field(None, max_length=10)
    roll_no:      Optional[str]  = Field(None, max_length=50)   # stored as registration_number
    designation:  Optional[str]  = Field(None, max_length=200)

    def to_user_kwargs(self) -> dict:
        data = self.model_dump(exclude={"roll_no"})
        data["registration_number"] = self.roll_no
        return data


class UserUpdate(BaseModel):
    name:         Optional[str]       = Field(None, min_length=1, max_length=200)
    email:        Optional[EmailStr]  = None
    role:         Optional[UserRole]  = None
    nickname:     Optional[str]       = Field(None, max_length=200)
    avatar_url:   Optional[str]       = Field(None, max_length=500)
    is_active:    Optional[bool]      = None
    department:   Optional[str]       = Field(None, max_length=200)
    year:         Optional[int]       = Field(None, ge=1, le=6)
    section:      Optional[str]       = Field(None, max_length=10)
    roll_no:      Optional[str]       = Field(None, max_length=50)   # stored as registration_number
    designation:  Optional[str]       = Field(None, max_length=200)

    def to_update_fields(self) -> dict:
        data = self.model_dump(exclude_unset=True, exclude={"roll_no"})
        if "roll_no" in self.model_fields_set:
            data["registration_number"] = self.roll_no
        return data


# ─────────────────────────────────────────────────────────────────────────────
# Helper: serialize User → safe dict
# ─────────────────────────────────────────────────────────────────────────────

def _serialize(u: User) -> dict:
    return {
        "id":                 str(u.id),
        "name":               u.name,
        "nickname":           u.nickname,
        "email":              u.email,
        "role":               u.role,
        "avatar_url":         u.avatar_url,
        "is_active":          u.is_active,
        "is_first_login":     u.is_first_login,
        "department":         u.department,
        "year":               u.year,
        "section":            u.section,
        "roll_no":            u.registration_number,   # registration_number exposed as roll_no
        "overall_attendance": u.overall_attendance,
        "designation":        u.designation,
        "created_at":         u.created_at.isoformat() if u.created_at else None,
        "updated_at":         u.updated_at.isoformat() if u.updated_at else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /students
# ─────────────────────────────────────────────────────────────────────────────

@router.get("")
def get_users(
    role:       Optional[UserRole] = Query(None, description="Filter by role: student | faculty | admin"),
    section:    Optional[str]      = Query(None, description="Filter by section, e.g. CSE06"),
    year:       Optional[int]      = Query(None, description="Filter by year, e.g. 4"),
    department: Optional[str]      = Query(None, description="Filter by department, e.g. CSE"),
    is_active:  Optional[bool]     = Query(None, description="Filter by active status"),
    db: Session = Depends(get_student_db),
):
    """Return users from the `users` table. Filter by role to separate students from faculty."""
    query = db.query(User)

    if role:
        query = query.filter(User.role == role)
    if section:
        query = query.filter(User.section == section)
    if year:
        query = query.filter(User.year == year)
    if department:
        query = query.filter(User.department == department)
    if is_active is not None:
        query = query.filter(User.is_active == is_active)

    users = query.order_by(User.name).all()

    logger.info(f"[students] fetched {len(users)} users (role={role})")
    return [_serialize(u) for u in users]


# ─────────────────────────────────────────────────────────────────────────────
# POST /students
# ─────────────────────────────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_student_db),
):
    """Create a new user. Prevents duplicate email and roll_no (registration_number)."""
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists.",
        )

    if payload.roll_no:
        if db.query(User).filter(User.registration_number == payload.roll_no).first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A user with this roll number already exists.",
            )

    user = User(**payload.to_user_kwargs())

    try:
        db.add(user)
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Integrity error — duplicate email or roll number.",
        )

    logger.info(f"[students] created user id={user.id} email={user.email}")
    return {"success": True, **_serialize(user)}


# ─────────────────────────────────────────────────────────────────────────────
# PUT /students/{user_id}
# ─────────────────────────────────────────────────────────────────────────────

@router.put("/{user_id}")
def update_user(
    user_id: str,
    payload: UserUpdate,
    db: Session = Depends(get_student_db),
):
    """Update an existing user by id. Returns 404 if not found."""
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{user_id}' not found.",
        )

    for field, value in payload.to_update_fields().items():
        setattr(user, field, value)

    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Update conflicts with an existing record (duplicate email or roll number).",
        )

    logger.info(f"[students] updated user id={user.id}")
    return {"success": True, **_serialize(user)}


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /students/{user_id}
# ─────────────────────────────────────────────────────────────────────────────

@router.delete("/{user_id}")
def delete_user(
    user_id: str,
    db: Session = Depends(get_student_db),
):
    """Delete a user by id. Returns 404 if not found."""
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{user_id}' not found.",
        )

    name = user.name
    db.delete(user)
    db.commit()

    logger.info(f"[students] deleted user id={user_id}")
    return {
        "success": True,
        "message": f"User '{name}' (id: {user_id}) deleted successfully.",
    }