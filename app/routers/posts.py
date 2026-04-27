from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, field_validator
from datetime import datetime, timezone  # ← added timezone

from app.database import get_db, get_master_db
from app.models import User, Post, MasterStudent
from app.schemas import MessageResponse
from app.auth.dependencies import get_current_user

router = APIRouter(prefix="/posts", tags=["Chat"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class ChatMessageRequest(BaseModel):
    """Request body for POST /posts."""
    message: str

    @field_validator("message")
    @classmethod
    def message_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Message cannot be blank.")
        if len(stripped) > 1000:
            raise ValueError("Message cannot exceed 1000 characters.")
        return stripped


class ChatMessageResponse(BaseModel):
    """
    Chat-style response.
    Maps Post columns: content → message, resolved full name → user_name.
    """
    id: str
    user_id: str
    user_name: str
    message: str
    created_at: datetime
    is_deleted: bool
    deleted_by: Optional[str] = None

    model_config = {
        "from_attributes": True,
        # ← Always serialize datetime as ISO 8601 with Z suffix
        "json_encoders": {datetime: lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%SZ")},
    }

    @classmethod
    def from_post(cls, post: Post, master_db: Session = None) -> "ChatMessageResponse":
        # Default to nickname; upgrade to full name if found in MasterStudent
        user_name = post.user.name if post.user else "Unknown"

        if master_db and post.user and post.user.email:
            student = master_db.query(MasterStudent).filter(
                MasterStudent.email == post.user.email
            ).first()
            user_name = student.fullname if student and student.fullname else user_name

        # ── Soft-delete message masking ───────────────────────────────────────
        if post.is_deleted:
            message = (
                "This message was deleted by admin"
                if post.deleted_by == "admin"
                else "This message was deleted"
            )
        else:
            message = post.content
        # ─────────────────────────────────────────────────────────────────────

        # ── Normalize naive DB timestamp → UTC-aware ──────────────────────────
        created_at = post.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        # ─────────────────────────────────────────────────────────────────────

        # ── Debug log ─────────────────────────────────────────────────────────
        print("DEBUG MESSAGE:")
        print({
            "user_email": post.user.email if post.user else None,
            "resolved_name": user_name,
            "message": message,
            "is_deleted": post.is_deleted,
            "deleted_by": post.deleted_by,
        })
        # ──────────────────────────────────────────────────────────────────────

        return cls(
            id=str(post.id),
            user_id=str(post.user_id),
            user_name=user_name,
            message=message,
            created_at=created_at,   # ← always UTC-aware
            is_deleted=post.is_deleted,
            deleted_by=post.deleted_by,
        )


# ── GET /posts/admin/messages-dev (NO AUTH — admin dashboard only) ────────────

@router.get("/admin/messages-dev", response_model=List[ChatMessageResponse])
def get_posts_admin_dev(
    search: Optional[str] = Query(None, description="Filter by message content"),
    user_id: Optional[str] = Query(None, description="Filter by sender user_id"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    master_db: Session = Depends(get_master_db),
):
    """Admin dev endpoint — no auth. Same as GET /posts but open for admin dashboard."""
    query = db.query(Post)

    if search:
        query = query.filter(Post.content.ilike(f"%{search.strip()}%"))
    if user_id:
        query = query.filter(Post.user_id == user_id)

    posts = (
        query
        .order_by(Post.created_at.asc())
        .offset((page - 1) * pageSize)
        .limit(pageSize)
        .all()
    )

    return [ChatMessageResponse.from_post(p, master_db) for p in posts]


# ── DELETE /posts/admin/delete-dev/{post_id} (NO AUTH — admin dashboard only) ─

@router.delete("/admin/delete-dev/{post_id}", response_model=MessageResponse)
def admin_delete_post_dev(
    post_id: str,
    db: Session = Depends(get_db),
):
    """
    Admin dev endpoint — no auth required.
    Soft-deletes the post: sets is_deleted, deleted_by='admin', deleted_at.
    Never removes the row from the database.
    """
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Message not found.")

    post.is_deleted = True
    post.deleted_by = "admin"
    post.deleted_at = datetime.now(timezone.utc)  # ← was datetime.utcnow()
    db.commit()

    return MessageResponse(message="Message deleted by admin.")


# ── GET /posts ────────────────────────────────────────────────────────────────

@router.get("", response_model=List[ChatMessageResponse])
def get_posts(
    search: Optional[str] = Query(None, description="Filter by message content"),
    user_id: Optional[str] = Query(None, description="Filter by sender user_id"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    master_db: Session = Depends(get_master_db),
):
    """Returns messages in chat order (oldest → newest)."""
    query = db.query(Post)

    if search:
        query = query.filter(Post.content.ilike(f"%{search.strip()}%"))
    if user_id:
        query = query.filter(Post.user_id == user_id)

    posts = (
        query
        .order_by(Post.created_at.asc())
        .offset((page - 1) * pageSize)
        .limit(pageSize)
        .all()
    )

    return [ChatMessageResponse.from_post(p, master_db) for p in posts]


# ── POST /posts ───────────────────────────────────────────────────────────────

@router.post("", response_model=ChatMessageResponse, status_code=status.HTTP_201_CREATED)
def create_post(
    payload: ChatMessageRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    master_db: Session = Depends(get_master_db),
):
    """Accepts { "message": "..." } and stores it in posts.content."""
    post = Post(
        user_id=current_user.id,
        content=payload.message,
    )
    db.add(post)
    db.commit()
    db.refresh(post)

    # Re-fetch with joined user so user.name / user.email are available
    post = db.query(Post).filter(Post.id == post.id).first()
    return ChatMessageResponse.from_post(post, master_db)


# ── GET /posts/{post_id} ──────────────────────────────────────────────────────

@router.get("/{post_id}", response_model=ChatMessageResponse)
def get_post(
    post_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    master_db: Session = Depends(get_master_db),
):
    """Fetch a single message by ID."""
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Message not found.")
    return ChatMessageResponse.from_post(post, master_db)


# ── DELETE /posts/{post_id} ───────────────────────────────────────────────────

@router.delete("/{post_id}", response_model=MessageResponse)
def delete_post(
    post_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Soft-delete a message. Only the sender or an admin may delete."""
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Message not found.")

    from app.models import UserRole  # local import to avoid circular risk
    if str(post.user_id) != str(current_user.id) and current_user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Not authorized to delete this message.")

    # ── Soft delete — never remove the row from the database ─────────────────
    post.is_deleted = True
    post.deleted_by = "admin" if current_user.role == UserRole.admin else "user"
    post.deleted_at = datetime.now(timezone.utc)  # ← was datetime.utcnow()
    db.commit()
    # ─────────────────────────────────────────────────────────────────────────

    return MessageResponse(message="Message deleted.")