from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import func
import uuid, os, pathlib

from app.database import get_db, get_master_db, get_faculty_db
from app.models import User, UserRole, MasterStudent
from app.models.faculty import FacultyMember
from app.schemas import UserProfileResponse, UpdateProfileRequest, AdminUpdateUserRequest, MessageResponse
from app.auth.dependencies import get_current_user, require_admin

router = APIRouter(prefix="/users", tags=["Users"])

BASE_URL = "https://universe-mainbackend.onrender.com"
AVATARS_DIR = pathlib.Path("uploads/avatars")
AVATARS_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


def _full_avatar_url(avatar_url: Optional[str]) -> Optional[str]:
    """Convert a relative avatar path to an absolute URL."""
    if not avatar_url:
        return None
    if avatar_url.startswith("http"):
        return avatar_url
    return f"{BASE_URL}{avatar_url}"


def _delete_avatar_file(avatar_url: Optional[str]) -> None:
    """Delete an avatar file from disk given its full or relative URL."""
    if not avatar_url:
        return
    # Strip base URL to get relative path, e.g. /uploads/avatars/abc.jpg
    relative = avatar_url.replace(BASE_URL, "").lstrip("/")
    full_path = pathlib.Path(relative)
    if full_path.exists() and full_path.is_file():
        full_path.unlink(missing_ok=True)


# ── Profile ───────────────────────────────────────────────────────────────────

@router.get("/me")
def get_my_profile(
    current_user: User = Depends(get_current_user),
    master_db: Session = Depends(get_master_db),
    faculty_db: Session = Depends(get_faculty_db),
):
    role  = current_user.role
    email = current_user.email

    if role == "faculty":
        faculty = faculty_db.query(FacultyMember).filter(
            func.lower(FacultyMember.email) == email.lower()
        ).first()
        if not faculty:
            raise HTTPException(status_code=404, detail="Faculty profile not found in faculty database")
        return {
            "id":          faculty.id,
            "name":        faculty.name,
            "email":       faculty.email,
            "department":  getattr(faculty, "department",  None),
            "designation": getattr(faculty, "designation", None),
            "avatarUrl":   _full_avatar_url(current_user.avatar_url),
            "role":        "faculty",
            "isActive":    current_user.is_active,
            "createdAt":   current_user.created_at,
        }

    if role == "student":
        student = master_db.query(MasterStudent).filter(
            func.lower(MasterStudent.email) == email.lower()
        ).first()
        if not student:
            raise HTTPException(status_code=404, detail="Student profile not found in master database")
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
            "avatarUrl":          _full_avatar_url(current_user.avatar_url),
            "role":               "student",
            "isActive":           current_user.is_active,
            "createdAt":          current_user.created_at,
        }

    return {
        "id":        current_user.id,
        "name":      current_user.name,
        "email":     email,
        "avatarUrl": _full_avatar_url(current_user.avatar_url),
        "role":      role,
        "isActive":  current_user.is_active,
        "createdAt": current_user.created_at,
    }


@router.patch("/me", response_model=UserProfileResponse)
def update_my_profile(
    payload: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
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


# ── Avatar Upload ─────────────────────────────────────────────────────────────

@router.post("/upload-avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Upload a new avatar.
    - Validates content-type and file size.
    - Deletes the old avatar file from disk (if any).
    - Saves the new file under uploads/avatars/<uuid>.<ext>.
    - Stores the full absolute URL in the DB.
    """
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{file.content_type}'. Allowed: jpeg, png, webp, gif.",
        )

    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large. Max 5 MB.")

    # Determine extension from content_type
    ext_map = {
        "image/jpeg": "jpg",
        "image/png":  "png",
        "image/webp": "webp",
        "image/gif":  "gif",
    }
    ext = ext_map[file.content_type]
    filename = f"{uuid.uuid4().hex}.{ext}"
    dest = AVATARS_DIR / filename

    # Delete old avatar from disk
    _delete_avatar_file(current_user.avatar_url)

    # Write new file
    dest.write_bytes(contents)

    # Build and persist full URL
    new_url = f"{BASE_URL}/uploads/avatars/{filename}"
    current_user.avatar_url = new_url
    db.commit()
    db.refresh(current_user)

    return {
        "avatarUrl": new_url,
        "message":   "Avatar updated successfully.",
    }


# ── Avatar Delete ─────────────────────────────────────────────────────────────

@router.delete("/avatar")
def delete_avatar(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove avatar file and clear avatar_url in DB."""
    if not current_user.avatar_url:
        raise HTTPException(status_code=404, detail="No avatar to delete.")

    _delete_avatar_file(current_user.avatar_url)
    current_user.avatar_url = None
    db.commit()

    return {"message": "Avatar deleted successfully."}


# ── Dev / Admin routes (unchanged) ────────────────────────────────────────────

@router.get("/faculty-dev", response_model=List[dict])
def get_all_faculty_dev():
    return [
        {"faculty_id": 6,  "name": "Faculty 6"},
        {"faculty_id": 8,  "name": "Faculty 8"},
        {"faculty_id": 10, "name": "Faculty 10"},
    ]


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


@router.get("/{user_id}", response_model=UserProfileResponse)
def get_user(
    user_id: str,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserProfileResponse.model_validate(user)


@router.patch("/{user_id}", response_model=UserProfileResponse)
def admin_update_user(
    user_id: str,
    payload: AdminUpdateUserRequest,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    for field, value in payload.model_dump(exclude_none=True).items():
        snake = {"isActive": "is_active"}.get(field, field)
        setattr(user, snake, value)
    db.commit()
    db.refresh(user)
    return UserProfileResponse.model_validate(user)


@router.delete("/{user_id}", response_model=MessageResponse)
def delete_user(
    user_id: str,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(user)
    db.commit()
    return MessageResponse(message="User deleted successfully")