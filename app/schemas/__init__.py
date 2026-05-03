"""
app/schemas/__init__.py

UniVerse — Pydantic v2 schemas.
Response models use camelCase for direct React Native consumption.
All new attendance schemas (mark, student fetch) are included here.
"""
from __future__ import annotations

from datetime import datetime, date, time
from typing import Optional, List, Any
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from app.models import UserRole, AnnouncementType, EventCategory, AttendanceStatus, JobStatus


# ─── Base Config ──────────────────────────────────────────────────────────────

class CamelModel(BaseModel):
    """Base model that serialises to camelCase for the React Native frontend."""
    model_config = {
        "populate_by_name": True,
        "from_attributes": True,
    }

    def model_dump(self, **kwargs):
        kwargs.setdefault("by_alias", True)
        return super().model_dump(**kwargs)

    def model_dump_json(self, **kwargs):
        kwargs.setdefault("by_alias", True)
        return super().model_dump_json(**kwargs)


def to_camel(string: str) -> str:
    components = string.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


# ─── Common Responses ─────────────────────────────────────────────────────────

class MessageResponse(BaseModel):
    message: str
    success: bool = True


class PaginatedResponse(BaseModel):
    items: List[Any]
    total: int
    page: int
    pageSize: int
    totalPages: int


# ══════════════════════════════════════════════════════════════════════════════
# AUTH SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class RegisterRequest(BaseModel):
    name:                str      = Field(..., min_length=2, max_length=200)
    email:               EmailStr
    password:            str      = Field(..., min_length=6)
    role:                UserRole = UserRole.student
    department:          Optional[str] = None
    year:                Optional[int] = Field(None, ge=1, le=6)
    section:             Optional[str] = None
    registration_number: Optional[str] = None
    designation:         Optional[str] = None


class LoginRequest(BaseModel):
    email:    EmailStr
    password: str


class ProfileDataResponse(BaseModel):
    """Minimal profile from the master database, returned alongside the token."""
    name:   str
    email:  str
    branch: Optional[str] = None
    year:   Optional[int] = None


class TokenResponse(BaseModel):
    accessToken:         str
    refreshToken:        str
    tokenType:           str = "Bearer"
    user:                "UserProfileResponse"
    login_status:        Optional[str]  = Field(None, alias="loginStatus")
    profile:             Optional[ProfileDataResponse] = None
    forcePasswordChange: Optional[bool] = None


class RefreshTokenRequest(BaseModel):
    refreshToken: str


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=6)
    new_password: str = Field(..., min_length=6)


class EmailOnlyRequest(BaseModel):
    email: EmailStr


class VerifyOtpRequest(BaseModel):
    email: EmailStr
    otp:   str = Field(..., min_length=6, max_length=6)


class ResetPasswordRequest(BaseModel):
    email:        EmailStr
    otp:          str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=6)


class GoogleLoginRequest(BaseModel):
    id_token: str = Field(..., alias="idToken")


# ══════════════════════════════════════════════════════════════════════════════
# USER SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class UserProfileResponse(CamelModel):
    id:         UUID
    name:       str
    email:      str
    role:       UserRole
    avatar_url: Optional[str]   = Field(None, alias="avatarUrl")
    nickname:   Optional[str]   = None          # short display name from master DB
    is_active:  bool            = Field(True,  alias="isActive")
    created_at: datetime        = Field(alias="createdAt")

    # Student fields
    department:          Optional[str]   = None
    year:                Optional[int]   = None
    section:             Optional[str]   = None
    registration_number: Optional[str]   = Field(None, alias="registrationNumber")
    overall_attendance:  Optional[float] = Field(None, alias="overallAttendance")

    # Faculty fields
    designation: Optional[str] = None

    model_config = {"populate_by_name": True, "from_attributes": True}


class UpdateProfileRequest(BaseModel):
    name:        Optional[str] = Field(None, min_length=2, max_length=200)
    department:  Optional[str] = None
    year:        Optional[int] = Field(None, ge=1, le=6)
    section:     Optional[str] = None
    designation: Optional[str] = None


class AdminUpdateUserRequest(UpdateProfileRequest):
    is_active: Optional[bool]     = None
    role:      Optional[UserRole] = None


# ══════════════════════════════════════════════════════════════════════════════
# ANNOUNCEMENT SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class CreateAnnouncementRequest(BaseModel):
    title:    str              = Field(..., min_length=3, max_length=200)
    body:     str              = Field(..., min_length=10)
    type:     AnnouncementType = AnnouncementType.general
    date:     Optional[date]   = None
    isUrgent: bool             = False


class AnnouncementResponse(BaseModel):
    """Matches frontend shape: { id, title, body, type, date, urgent, createdBy }"""
    id:        UUID
    title:     str
    body:      str
    type:      str
    date:      date
    urgent:    bool             # frontend uses 'urgent', not 'isUrgent'
    createdBy: Optional[str] = None

    model_config = {"from_attributes": False}

    @classmethod
    def from_orm(cls, obj: Any) -> "AnnouncementResponse":
        return cls(
            id=obj.id,
            title=obj.title,
            body=obj.body,
            type=obj.type.value if hasattr(obj.type, "value") else obj.type,
            date=obj.date,
            urgent=obj.is_urgent,
            createdBy=obj.creator.name if obj.creator else None,
        )


# ══════════════════════════════════════════════════════════════════════════════
# EVENT SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class CreateEventRequest(BaseModel):
    title:       str            = Field(..., min_length=3, max_length=300)
    description: Optional[str] = None
    date:        date                            # required — frontend always sends this
    time:        Optional[time] = None           # optional — DB column is now nullable
    venue:       Optional[str] = None
    location:    Optional[str] = None            # alias from admin UI; router maps → venue
    # FIX: changed from EventCategory enum type to str so that category values
    #      not yet in the Python enum (e.g. "workshop", "seminar") don't cause
    #      a 422 Unprocessable Entity. The normalise_category validator below
    #      lowercases the value; the DB column is now String(50).
    category:    str            = Field("technical")
    totalSlots:  int            = Field(100, ge=1)
    form_url:    Optional[str] = None            # Google Form registration URL

    @field_validator("date", mode="before")
    @classmethod
    def parse_date(cls, v: Any) -> Any:
        """Accept 'YYYY-MM-DD' strings from the frontend — Pydantic v2 needs this coercion."""
        if isinstance(v, str):
            from datetime import date as _date
            try:
                return _date.fromisoformat(v)
            except ValueError:
                raise ValueError(f"Invalid date format '{v}'. Expected YYYY-MM-DD.")
        return v

    @field_validator("time", mode="before")
    @classmethod
    def parse_time(cls, v: Any) -> Any:
        """
        Accept 'HH:MM' or 'HH:MM:SS' strings — frontend typically omits seconds.
        Empty string and None both map to None (column is nullable).
        """
        if v is None or v == "":
            return None
        if isinstance(v, str):
            for fmt in ("%H:%M:%S", "%H:%M"):
                try:
                    from datetime import datetime
                    return datetime.strptime(v, fmt).time()
                except ValueError:
                    continue
        return v


# ══════════════════════════════════════════════════════════════════════════════
# ATTENDANCE SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class AttendanceMarkRequest(BaseModel):
    subject:     str
    date:        date
    time_slot:   Optional[str] = None
    section:     Optional[str] = None
    year:        Optional[int] = None
    department:  Optional[str] = None
    records:     List["AttendanceRecord"]

    @field_validator("subject", mode="before")
    @classmethod
    def strip_subject(cls, v: str) -> str:
        return v.strip()


class AttendanceMarkResponse(BaseModel):
    """Return value for POST /attendance/mark."""
    message: str
    success: bool = True
    created: int  = Field(..., description="New records inserted")
    updated: int  = Field(..., description="Existing records updated (status changed)")
    skipped: int  = Field(..., description="Records with no change (skipped)")


class SubjectSummaryItem(BaseModel):
    """Running totals per subject — used inside StudentAttendanceResponse."""
    subject:    str
    present:    int
    total:      int
    percentage: float


class DayAttendanceItem(BaseModel):
    """One class entry in a student's day-wise list."""
    date:      date
    subject:   str
    time_slot: Optional[str]
    status:    str              # "present" | "absent"
    section:   Optional[str]   = None
    marked_by: Optional[str]   = None

    model_config = {"from_attributes": False}

    @classmethod
    def from_orm(cls, obj: Any) -> "DayAttendanceItem":
        return cls(
            date=obj.date,
            subject=obj.subject,
            time_slot=getattr(obj, "time_slot", None),
            status=(
                obj.status.value
                if hasattr(obj.status, "value")
                else str(obj.status)
            ),
            section=getattr(obj, "section", None),
            marked_by=obj.faculty.name if getattr(obj, "faculty", None) else None,
        )


class StudentAttendanceResponse(BaseModel):
    """
    Full attendance payload for GET /attendance/student/{student_id}.

    Fields
    ------
    registration_number  – the queried student's reg number
    overall_percentage   – aggregate across all subjects (unfiltered)
    total_classes        – total across all subjects
    attended_classes     – present count across all subjects
    subjects             – per-subject breakdown (always full totals)
    records              – day-wise list (filtered by month/year if requested)
    """
    registration_number: str
    overall_percentage:  float
    total_classes:       int
    attended_classes:    int
    subjects:            List[SubjectSummaryItem]
    records:             List[DayAttendanceItem]


# ══════════════════════════════════════════════════════════════════════════════
# POST / SOCIAL FEED SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class CreatePostRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000)


class CommentRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=1000)


class CommentResponse(BaseModel):
    id:         UUID
    userName:   str
    userRole:   str
    content:    str
    timePosted: str

    model_config = {"from_attributes": False}

    @classmethod
    def from_orm(cls, obj: Any) -> "CommentResponse":
        return cls(
            id=obj.id,
            userName=obj.user.name,
            userRole=obj.user.role.value if hasattr(obj.user.role, "value") else obj.user.role,
            content=obj.content,
            timePosted=obj.created_at.isoformat(),
        )


class PostResponse(BaseModel):
    """Matches frontend: { id, userName, userRole, content, timePosted, likes, comments }"""
    id:         UUID
    userName:   str
    userRole:   str
    content:    str
    timePosted: str
    likes:      int
    comments:   int
    userAvatar: Optional[str] = None
    isLiked:    bool          = False

    model_config = {"from_attributes": False}

    @classmethod
    def from_orm_with_user(cls, obj: Any, user_id: Optional[UUID] = None) -> "PostResponse":
        is_liked = False
        if user_id:
            is_liked = any(str(u.id) == str(user_id) for u in obj.liked_by)
        return cls(
            id=obj.id,
            userName=obj.user.name,
            userRole=obj.user.role.value if hasattr(obj.user.role, "value") else obj.user.role,
            content=obj.content,
            timePosted=obj.created_at.isoformat(),
            likes=obj.likes_count,
            comments=obj.comments_count,
            userAvatar=obj.user.avatar_url,
            isLiked=is_liked,
        )


# ══════════════════════════════════════════════════════════════════════════════
# TIMETABLE SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class CreateTimetableRequest(BaseModel):
    day:        str            = Field(..., description="Monday, Tuesday, …")
    subject:    str
    startTime:  time
    endTime:    time
    facultyId:  Optional[UUID] = None
    room:       Optional[str]  = None
    department: Optional[str]  = None
    section:    Optional[str]  = None
    year:       Optional[int]  = None


class TimetableResponse(BaseModel):
    id:          UUID
    day:         str
    subject:     str
    startTime:   str
    endTime:     str
    facultyName: Optional[str] = None
    room:        Optional[str] = None
    department:  Optional[str] = None
    section:     Optional[str] = None
    year:        Optional[int] = None

    model_config = {"from_attributes": False}

    @classmethod
    def from_orm(cls, obj: Any) -> "TimetableResponse":
        return cls(
            id=obj.id,
            day=obj.day,
            subject=obj.subject,
            startTime=obj.start_time.strftime("%H:%M"),
            endTime=obj.end_time.strftime("%H:%M"),
            facultyName=obj.faculty.name if obj.faculty else None,
            room=obj.room,
            department=obj.department,
            section=obj.section,
            year=obj.year,
        )


# ══════════════════════════════════════════════════════════════════════════════
# JOB SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class CreateJobRequest(BaseModel):
    companyName: str            = Field(..., min_length=2)
    role:        str
    package:     Optional[str]  = None
    deadline:    date
    description: Optional[str]  = None


class JobResponse(BaseModel):
    id:             UUID
    companyName:    str
    role:           str
    package:        Optional[str]
    deadline:       date
    description:    Optional[str]
    status:         str
    applied:        bool = False
    applicantCount: int  = 0

    model_config = {"from_attributes": False}

    @classmethod
    def from_orm_with_user(cls, obj: Any, user_id: Optional[UUID] = None) -> "JobResponse":
        applied = False
        if user_id:
            applied = any(str(u.id) == str(user_id) for u in obj.applicants)
        return cls(
            id=obj.id,
            companyName=obj.company_name,
            role=obj.role,
            package=obj.package,
            deadline=obj.deadline,
            description=obj.description,
            status=obj.status.value if hasattr(obj.status, "value") else obj.status,
            applied=applied,
            applicantCount=len(obj.applicants),
        )


# ══════════════════════════════════════════════════════════════════════════════
# NOTIFICATION SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class NotificationResponse(BaseModel):
    id:        UUID
    title:     str
    message:   str
    isRead:    bool
    createdAt: str

    model_config = {"from_attributes": False}

    @classmethod
    def from_orm(cls, obj: Any) -> "NotificationResponse":
        return cls(
            id=obj.id,
            title=obj.title,
            message=obj.message,
            isRead=obj.is_read,
            createdAt=obj.created_at.isoformat(),
        )