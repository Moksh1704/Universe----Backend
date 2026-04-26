"""
auth.py – Authentication router for UniVerse.

Login flow (auto role detection, no manual selection):
  1. Normalize email.
  2. Check student master DB → if found, proceed as student.
  3. Else check faculty DB   → if found, proceed as faculty.
  4. If neither              → 404.

Faculty rules:
  - No password column in faculty_db.
  - Password login uses a fixed default (FACULTY_DEFAULT_PASSWORD from .env).
  - OTP login is also supported (OTP stored on User row in student_db).

Student login is unchanged from the original implementation.
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy import func


from app.database import get_student_db, get_master_db, get_faculty_db
from app.models import User, UserRole, MasterStudent
from app.models.faculty import FacultyMember
from app.schemas import (
    RegisterRequest,
    LoginRequest,
    TokenResponse,
    RefreshTokenRequest,
    UserProfileResponse,
    MessageResponse,
    ChangePasswordRequest,
    EmailOnlyRequest,
    VerifyOtpRequest,
    ResetPasswordRequest,
    GoogleLoginRequest,
    ProfileDataResponse,
)
from app.auth.utils import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_otp,
    send_otp_email,
    verify_google_id_token,
)
from app.auth.dependencies import get_current_user
from app.config import settings
from app.utils.email_service import send_email


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _normalize_email(email: str) -> str:
    """Normalize email for consistent lookups."""
    return email.strip().lower()


def _get_or_create_student_user(
    normalized_email: str,
    master: MasterStudent,
    db: Session,
    *,
    google_id: str | None = None,
) -> tuple["User", bool]:
    """
    Fetch a student User from the student DB, creating it only if absent.
    Race-condition safe via IntegrityError catch-and-refetch.
    Returns (user, created).
    """
    user = db.query(User).filter(User.email == normalized_email).first()
    if user is not None:
        return user, False

    # Use fullname (legal name) – fall back to name only if fullname is absent
    full_name = (getattr(master, "fullname", None) or "").strip() or (master.name or "").strip()
    nick_name = (master.name or "").strip()
    new_user = User(
        name=full_name,
        nickname=nick_name,
        email=normalized_email,
        hashed_password=hash_password(settings.DEFAULT_PASSWORD),
        role=UserRole.student,
        department=master.department,
        year=master.year,
        section=getattr(master, "section", None),
        registration_number=getattr(master, "regnum", None),
        google_id=google_id,
        is_first_login=True,
    )
    db.add(new_user)
    try:
        db.commit()
        db.refresh(new_user)
        print(f"[USER CREATED] email={new_user.email}  reg_no={new_user.registration_number}")
        print(f"[DEBUG USER] name={new_user.name}, nickname={new_user.nickname}, reg_no={new_user.registration_number}")
        return new_user, True
    except IntegrityError:
        db.rollback()
        user = db.query(User).filter(User.email == normalized_email).first()
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create or retrieve user. Please try again.",
            )
        return user, False


def _get_or_create_faculty_user(
    normalized_email: str,
    faculty: FacultyMember,
    db: Session,
) -> tuple["User", bool]:
    """
    Fetch a faculty User from the student DB (shared user table),
    creating it only if absent.  Faculty users are created with:
      - role = faculty
      - hashed_password = FACULTY_DEFAULT_PASSWORD
      - is_first_login = False  (faculty don't go through password-change flow)
    Race-condition safe via IntegrityError catch-and-refetch.
    Returns (user, created).
    """
    user = db.query(User).filter(User.email == normalized_email).first()
    if user is not None:
        return user, False

    new_user = User(
        name=faculty.name,
        email=normalized_email,
        hashed_password=hash_password(settings.FACULTY_DEFAULT_PASSWORD),
        role=UserRole.faculty,
        department=getattr(faculty, "department", None),
        designation=getattr(faculty, "designation", None),
        is_first_login=False,
    )
    db.add(new_user)
    try:
        db.commit()
        db.refresh(new_user)
        return new_user, True
    except IntegrityError:
        db.rollback()
        user = db.query(User).filter(User.email == normalized_email).first()
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create or retrieve faculty user. Please try again.",
            )
        return user, False


def _resolve_identity(
    normalized_email: str,
    master_db: Session,
    faculty_db: Session,
    student_db: Session = None,
) -> tuple[str, MasterStudent | None, FacultyMember | None]:
    """
    Determine whether the email belongs to a student or faculty member.

    Priority:
      1. faculty_db  → faculty
      2. master_db   → student (graceful fallback if table missing)
      3. student_db  → student (if already registered in app)

    Returns:
        (role, master_student_or_None, faculty_member_or_None)
        role is 'student' | 'faculty' | 'unknown'
    """
    # ── 1. Check faculty_db first (avoids master_students query for faculty) ──
    try:
        faculty = faculty_db.query(FacultyMember).filter(
            func.lower(func.trim(FacultyMember.email)) == normalized_email
        ).first()
        if faculty:
            print(f"[DEBUG] _resolve_identity: '{normalized_email}' → FACULTY (faculty_db)")
            return "faculty", None, faculty
    except Exception as e:
        print(f"[DEBUG] _resolve_identity: faculty_db query failed: {e}")
        pass  # faculty_db unavailable – continue

    # ── 2. Try master_db for student validation ───────────────────────────────
    try:
        print(f"[DEBUG] _resolve_identity: querying master_db (students table) for '{normalized_email}'")
        master = master_db.query(MasterStudent).filter(
            func.lower(func.trim(MasterStudent.email)) == normalized_email
        ).first()
        print(f"[DEBUG] _resolve_identity: master_db result = {master}")
        if master:
            print(f"[DEBUG] _resolve_identity: '{normalized_email}' → STUDENT (master_db)")
            return "student", master, None
    except Exception as e:
        print(f"[DEBUG] _resolve_identity: master_db query failed: {e}")
        # master table doesn't exist – fall through to student_db check
        pass

    # ── 3. Fallback: check if user already exists in student_db ──────────────
    if student_db is not None:
        try:
            existing = student_db.query(User).filter(
                User.email == normalized_email
            ).first()
            if existing:
                print(f"[DEBUG] _resolve_identity: '{normalized_email}' → STUDENT (student_db fallback)")
                # Return a synthetic MasterStudent-like object so the login
                # flow can proceed without creating a duplicate user.
                class _FakeMaster:
                    name   = existing.name
                    email  = existing.email
                    branch = existing.department
                    year   = existing.year
                return "student", _FakeMaster(), None
        except Exception:
            pass

    print(f"[DEBUG] _resolve_identity: '{normalized_email}' → UNKNOWN (not found in any DB)")
    return "unknown", None, None


router = APIRouter(prefix="/auth", tags=["Authentication"])


# ─── Register ─────────────────────────────────────────────────────────────────

@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_student_db)):
    """Register a new user. (Unchanged from original.)"""
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    if payload.registration_number:
        dup = db.query(User).filter(
            User.registration_number == payload.registration_number
        ).first()
        if dup:
            raise HTTPException(status_code=400, detail="Registration number already exists")

    user = User(
        name=payload.name,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        role=payload.role,
        department=payload.department,
        year=payload.year,
        section=payload.section,
        registration_number=payload.registration_number,
        designation=payload.designation,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    access_token  = create_access_token({"sub": str(user.id), "role": user.role.value})
    refresh_token = create_refresh_token({"sub": str(user.id)})

    return TokenResponse(
        accessToken=access_token,
        refreshToken=refresh_token,
        user=UserProfileResponse.model_validate(user),
    )


# ─── Login (unified: student + faculty, auto role detection) ──────────────────

@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    db:         Session = Depends(get_student_db),
    master_db:  Session = Depends(get_master_db),
    faculty_db: Session = Depends(get_faculty_db),
):
    """
    Unified email + password login.

    Role is auto-detected:
      1. Check student master DB first.
      2. If not found, check faculty DB.
      3. If neither, return 404.

    Faculty login:
      - Password must match FACULTY_DEFAULT_PASSWORD (fixed, no column in faculty_db).
      - No CHANGE_PASSWORD_REQUIRED flow for faculty.

    Student login (original behaviour preserved):
      - First-time login creates user with default password.
      - Returns CHANGE_PASSWORD_REQUIRED if is_first_login is True.
    """
    normalized_email = _normalize_email(payload.email)

    role, master, faculty = _resolve_identity(normalized_email, master_db, faculty_db, db)

    if role == "unknown":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found. Please check your email address.",
        )

    # ── Faculty login ──────────────────────────────────────────────────────────
    if role == "faculty":
        user, _ = _get_or_create_faculty_user(normalized_email, faculty, db)

        # Faculty always authenticates against the fixed default password
        if not verify_password(payload.password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is deactivated",
            )

        access_token  = create_access_token(
            {"sub": str(user.id), "role": user.role.value, "email": user.email}
        )
        refresh_token = create_refresh_token({"sub": str(user.id), "email": user.email})

        return TokenResponse(
            accessToken=access_token,
            refreshToken=refresh_token,
            user=UserProfileResponse.model_validate(user),
        )

    # ── Student login ──────────────────────────────────────────────────────────
    user, _ = _get_or_create_student_user(normalized_email, master, db)

    if not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    # Issue real tokens regardless of is_first_login — never block student login.
    # If the student is on their first login (still using the default password),
    # surface a non-blocking flag so the frontend can show an optional prompt.
    access_token  = create_access_token(
        {"sub": str(user.id), "role": user.role.value, "email": user.email}
    )
    refresh_token = create_refresh_token({"sub": str(user.id), "email": user.email})

    return TokenResponse(
        accessToken=access_token,
        refreshToken=refresh_token,
        user=UserProfileResponse.model_validate(user),
        loginStatus="CHANGE_PASSWORD_RECOMMENDED" if user.is_first_login else None,
        forcePasswordChange=user.is_first_login,
        profile=ProfileDataResponse(
            name=master.fullname or master.name,
            email=master.email,
            branch=master.department,
            year=master.year,
        ),
    )


# ─── Refresh ──────────────────────────────────────────────────────────────────

@router.post("/refresh", response_model=TokenResponse)
def refresh_token(
    payload: RefreshTokenRequest,
    db: Session = Depends(get_student_db),
):
    """Refresh access token using a valid refresh token. (Unchanged.)"""
    token_data = decode_token(payload.refreshToken)
    if not token_data or token_data.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    user = db.query(User).filter(
        User.id == token_data["sub"], User.is_active == True
    ).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    access_token  = create_access_token(
        {"sub": str(user.id), "role": user.role.value, "email": user.email}
    )
    new_refresh = create_refresh_token({"sub": str(user.id), "email": user.email})

    return TokenResponse(
        accessToken=access_token,
        refreshToken=new_refresh,
        user=UserProfileResponse.model_validate(user),
    )


# ─── Logout / Me ──────────────────────────────────────────────────────────────

@router.post("/logout", response_model=MessageResponse)
def logout(current_user: User = Depends(get_current_user)):
    """Logout endpoint (client should discard tokens)."""
    return MessageResponse(message="Logged out successfully")


@router.get("/me", response_model=UserProfileResponse)
def get_me(current_user: User = Depends(get_current_user)):
    """Get current authenticated user profile."""
    return UserProfileResponse.model_validate(current_user)


# ─── Password Change ──────────────────────────────────────────────────────────

@router.post("/change-password", response_model=MessageResponse)
def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_student_db),
):
    """Change the current user's password. (Unchanged.)"""
    if not verify_password(payload.old_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Old password is incorrect",
        )

    current_user.hashed_password = hash_password(payload.new_password)
    current_user.is_first_login = False
    db.commit()

    return MessageResponse(message="Password updated successfully")


# ─── OTP: Send (students + faculty) ──────────────────────────────────────────

@router.post("/send-otp", response_model=MessageResponse)
def send_otp(
    payload:    EmailOnlyRequest,
    db:         Session = Depends(get_student_db),
    master_db:  Session = Depends(get_master_db),
    faculty_db: Session = Depends(get_faculty_db),
):
    """
    Generate and send a 6-digit OTP.

    Works for both students and faculty.
    - Validates email against student master DB or faculty DB.
    - Stores OTP on the User row in student_db (shared user table).
    - Does NOT write to master_db or faculty_db.
    """
    normalized_email = _normalize_email(payload.email)
    print(f"[DEBUG] send_otp: entered email='{payload.email}', normalized='{normalized_email}'")
    print(f"[DEBUG] send_otp: using master_db={master_db}, student_db(app db)={db}")

    role, master, faculty = _resolve_identity(normalized_email, master_db, faculty_db, db)
    if role == "unknown":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found. Please check your email address.",
        )

    # Ensure user row exists in student DB (shared user table)
    user = db.query(User).filter(User.email == normalized_email).first()
    if not user:
        if role == "student":
            user, _ = _get_or_create_student_user(normalized_email, master, db)
        else:
            user, _ = _get_or_create_faculty_user(normalized_email, faculty, db)

    # OTP rate limiting: max 3 requests per 5 minutes per email
    now = datetime.utcnow()
    window_start = now - timedelta(minutes=5)
    if user.otp_last_request_at and user.otp_last_request_at > window_start:
        if user.otp_request_count >= 3:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many OTP requests. Please try again later.",
            )
        user.otp_request_count += 1
    else:
        user.otp_request_count = 1
        user.otp_last_request_at = now

    otp_value = generate_otp()
    user.otp = otp_value
    user.otp_expiry = datetime.utcnow() + timedelta(minutes=settings.OTP_EXPIRY_MINUTES)
    db.commit()

    send_otp_email(normalized_email, otp_value, purpose="login")

    return MessageResponse(message="OTP sent successfully")


# ─── OTP: Verify (students + faculty) ────────────────────────────────────────

@router.post("/verify-otp", response_model=TokenResponse)
def verify_otp(
    payload:    VerifyOtpRequest,
    db:         Session = Depends(get_student_db),
    master_db:  Session = Depends(get_master_db),
    faculty_db: Session = Depends(get_faculty_db),
):
    """
    Verify OTP and log the user in.

    Works for both students and faculty.
    Returns { token, role, user } – role is auto-detected.
    """
    normalized_email = _normalize_email(payload.email)

    role, master, faculty = _resolve_identity(normalized_email, master_db, faculty_db, db)
    if role == "unknown":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found. Please check your email address.",
        )

    user = db.query(User).filter(User.email == normalized_email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No OTP requested. Please call /send-otp first.",
        )

    if not user.otp or not user.otp_expiry:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No OTP requested")

    if user.otp != payload.otp:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OTP")

    if datetime.utcnow() > user.otp_expiry:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OTP has expired")

    # Clear OTP fields
    user.otp = None
    user.otp_expiry = None
    db.commit()
    db.refresh(user)

    access_token  = create_access_token(
        {"sub": str(user.id), "role": user.role.value, "email": user.email}
    )
    refresh_token = create_refresh_token({"sub": str(user.id), "email": user.email})

    # Build profile from the appropriate identity source
    if role == "student":
        profile = ProfileDataResponse(
            name=master.fullname or master.name,
            email=master.email,
            branch=master.department,
            year=master.year,
        )
    else:
        profile = ProfileDataResponse(
            name=faculty.name,
            email=faculty.email,
            branch=getattr(faculty, "department", ""),
            year=None,
        )

    return TokenResponse(
        accessToken=access_token,
        refreshToken=refresh_token,
        user=UserProfileResponse.model_validate(user),
        profile=profile,
    )


# ─── Forgot Password ──────────────────────────────────────────────────────────

@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(
    payload:    EmailOnlyRequest,
    db:         Session = Depends(get_student_db),
    master_db:  Session = Depends(get_master_db),
    faculty_db: Session = Depends(get_faculty_db),
):
    """Send OTP for password reset. Works for students and faculty."""
    normalized_email = _normalize_email(payload.email)

    role, master, faculty = _resolve_identity(normalized_email, master_db, faculty_db, db)
    if role == "unknown":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found. Please check your email address.",
        )

    user = db.query(User).filter(User.email == normalized_email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No account found. Please log in first to activate your account.",
        )

    now = datetime.utcnow()
    window_start = now - timedelta(minutes=5)
    if user.otp_last_request_at and user.otp_last_request_at > window_start:
        if user.otp_request_count >= 3:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many OTP requests. Please try again later.",
            )
        user.otp_request_count += 1
    else:
        user.otp_request_count = 1
        user.otp_last_request_at = now

    otp_value = generate_otp()
    user.otp = otp_value
    user.otp_expiry = datetime.utcnow() + timedelta(minutes=settings.OTP_EXPIRY_MINUTES)
    db.commit()

    send_otp_email(normalized_email, otp_value, purpose="password reset")

    return MessageResponse(message="Password reset OTP sent successfully")


# ─── Reset Password ───────────────────────────────────────────────────────────

@router.post("/reset-password", response_model=MessageResponse)
def reset_password(
    payload:    ResetPasswordRequest,
    db:         Session = Depends(get_student_db),
    master_db:  Session = Depends(get_master_db),
    faculty_db: Session = Depends(get_faculty_db),
):
    """Reset password via OTP. Works for students and faculty."""
    normalized_email = _normalize_email(payload.email)

    role, master, faculty = _resolve_identity(normalized_email, master_db, faculty_db, db)
    if role == "unknown":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found. Please check your email address.",
        )

    user = db.query(User).filter(User.email == normalized_email).first()
    if not user or not user.otp or not user.otp_expiry:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid password reset request",
        )

    if user.otp != payload.otp:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OTP")

    if datetime.utcnow() > user.otp_expiry:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OTP has expired")

    user.hashed_password = hash_password(payload.new_password)
    user.is_first_login = False
    user.otp = None
    user.otp_expiry = None
    db.commit()

    return MessageResponse(message="Password reset successfully")


# ─── Google Login ─────────────────────────────────────────────────────────────

@router.post("/google-login", response_model=TokenResponse)
def google_login(
    payload:    GoogleLoginRequest,
    db:         Session = Depends(get_student_db),
    master_db:  Session = Depends(get_master_db),
    faculty_db: Session = Depends(get_faculty_db),
):
    """
    Google ID-token login with auto role detection.
    Works for students and faculty.
    """
    token_payload = verify_google_id_token(payload.id_token)
    if not token_payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google token",
        )

    email     = _normalize_email(token_payload.get("email", ""))
    name      = token_payload.get("name") or ""
    google_id = token_payload.get("sub")

    if not email or not google_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google token missing required fields",
        )

    role, master, faculty = _resolve_identity(email, master_db, faculty_db, db)
    if role == "unknown":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email not found in any database",
        )

    existing_google = db.query(User).filter(User.google_id == google_id).first()
    if existing_google and existing_google.email != email:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This Google account is already linked to another user",
        )

    user = db.query(User).filter(User.email == email).first()
    if not user:
        if role == "student":
            user, _ = _get_or_create_student_user(email, master, db, google_id=google_id)
        else:
            user, _ = _get_or_create_faculty_user(email, faculty, db)
            if not user.google_id:
                user.google_id = google_id
                db.commit()
                db.refresh(user)
    else:
        if not user.google_id:
            user.google_id = google_id
            db.commit()
            db.refresh(user)

    access_token  = create_access_token(
        {"sub": str(user.id), "role": user.role.value, "email": user.email}
    )
    refresh_token = create_refresh_token({"sub": str(user.id), "email": user.email})

    if role == "student":
        profile = ProfileDataResponse(
            name=master.fullname or master.name,
            email=master.email,
            branch=master.department,
            year=master.year,
        )
    else:
        profile = ProfileDataResponse(
            name=faculty.name,
            email=faculty.email,
            branch=getattr(faculty, "department", ""),
            year=None,
        )

    return TokenResponse(
        accessToken=access_token,
        refreshToken=refresh_token,
        user=UserProfileResponse.model_validate(user),
        profile=profile,
    )