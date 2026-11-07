"""Profile images and rider verification documents.

Profile images are shown to anyone, so they are decoded and re-encoded: that
removes EXIF data such as GPS, which could otherwise reveal where a rider lives.
Documents are stored unchanged and only an admin can open them.
Object keys are always generated here, so a filename can never choose a path.
"""

import io
import logging
import uuid

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import storage
from app.config import get_settings
from app.db import utcnow
from app.errors import AppError, conflict, forbidden, not_found
from app.models import (
    DocumentStatus,
    DocumentType,
    ProfileImage,
    RiderProfile,
    User,
    VerificationDocument,
    VerificationStatus,
)
from app.rate_limit import limiter
from app.services import audit

logger = logging.getLogger(__name__)

IMAGE_FORMATS = {
    "JPEG": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
    "WEBP": ("image/webp", "webp"),
}
PDF = ("application/pdf", "pdf")
MAX_PIXELS = 40_000_000
PROFILE_SIDE = 1024
REQUIRED_DOCUMENTS = (DocumentType.RIDER_CARD, DocumentType.DRIVING_LICENCE)


def _check_size(data: bytes) -> None:
    if not data:
        raise AppError(422, "EMPTY_FILE", "The file is empty")
    limit = get_settings().max_upload_bytes
    if len(data) > limit:
        raise AppError(413, "FILE_TOO_LARGE", f"Files can be at most {limit // 1_048_576} MB")


def _open_image(data: bytes) -> Image.Image:
    """Judges the file by its content. The client's filename and type are ignored."""
    try:
        image = Image.open(io.BytesIO(data))
        if image.format not in IMAGE_FORMATS or image.width * image.height > MAX_PIXELS:
            raise ValueError(image.format)
        return image
    except (
        UnidentifiedImageError,
        ValueError,
        OSError,
        SyntaxError,
        Image.DecompressionBombError,
    ) as exc:
        raise AppError(422, "BAD_IMAGE", "Upload a JPEG, PNG or WebP image") from exc


def _clean_profile_image(data: bytes) -> bytes:
    image = _open_image(data)
    try:
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((PROFILE_SIDE, PROFILE_SIDE))
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=85)
    except (ValueError, OSError, SyntaxError) as exc:
        raise AppError(422, "BAD_IMAGE", "This image could not be read") from exc
    return output.getvalue()


def _document_format(data: bytes) -> tuple[str, str]:
    if data.startswith(b"%PDF-"):
        return PDF
    image = _open_image(data)
    try:
        image.verify()
    except (ValueError, OSError, SyntaxError) as exc:
        raise AppError(422, "BAD_IMAGE", "This image could not be read") from exc
    return IMAGE_FORMATS[image.format]


def _require_confirmed(user: User) -> None:
    if user.phone_confirmed_at is None:
        raise forbidden("Confirm your phone number before uploading files")


def profile_urls(db: Session, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Pages still load, without photos, when storage is not set up."""
    if not user_ids or not storage.is_configured():
        return {}
    rows = db.execute(
        select(ProfileImage.user_id, ProfileImage.storage_key).where(
            ProfileImage.user_id.in_(user_ids)
        )
    ).all()
    ttl = get_settings().profile_image_url_ttl_seconds
    store = storage.get_storage()
    return {user_id: store.presigned_url(key, ttl) for user_id, key in rows}


def profile_url(db: Session, user_id: uuid.UUID) -> str | None:
    return profile_urls(db, [user_id]).get(user_id)


def _remove_quietly(key: str) -> None:
    """An orphaned file is harmless. It must not fail a request that already worked."""
    try:
        storage.get_storage().delete(key)
    except AppError:
        logger.warning("Could not delete stored file %s", key)


def save_profile_image(db: Session, user: User, data: bytes) -> str:
    _require_confirmed(user)
    limiter.hit(f"profile-image:{user.id}", 20, 3600)
    _check_size(data)
    cleaned = _clean_profile_image(data)
    store = storage.get_storage()
    key = f"profile-images/{user.id}/{uuid.uuid4()}.jpg"
    store.put(key, cleaned, "image/jpeg")

    row = db.get(ProfileImage, user.id)
    old_key = row.storage_key if row else None
    if row:
        row.storage_key = key
    else:
        db.add(ProfileImage(user_id=user.id, storage_key=key))
    db.commit()
    if old_key:
        _remove_quietly(old_key)
    return store.presigned_url(key, get_settings().profile_image_url_ttl_seconds)


def delete_profile_image(db: Session, user: User) -> None:
    row = db.get(ProfileImage, user.id)
    if row is None:
        raise not_found("Profile image")
    key = row.storage_key
    db.delete(row)
    db.commit()
    _remove_quietly(key)


def upload_document(
    db: Session, user: User, document_type: DocumentType, data: bytes
) -> VerificationDocument:
    _require_confirmed(user)
    if db.get(RiderProfile, user.id) is None:
        raise AppError(404, "NO_PROFILE", "Create your rider profile first")
    limiter.hit(f"document:{user.id}", 10, 3600)
    _check_size(data)
    content_type, extension = _document_format(data)

    document_id = uuid.uuid4()
    folder = document_type.lower().replace("_", "-")
    key = f"verification-documents/{user.id}/{folder}/{document_id}.{extension}"
    storage.get_storage().put(key, data, content_type)

    document = VerificationDocument(
        id=document_id,
        rider_id=user.id,
        document_type=document_type,
        storage_key=key,
        content_type=content_type,
        size_bytes=len(data),
    )
    db.add(document)
    audit.record(db, user.id, "document.uploaded", "document", document_id, type=document_type)
    db.commit()
    return document


def my_documents(db: Session, user: User) -> list[VerificationDocument]:
    return list(
        db.scalars(
            select(VerificationDocument)
            .where(VerificationDocument.rider_id == user.id)
            .order_by(VerificationDocument.created_at.desc())
        )
    )


def list_for_review(
    db: Session, status: DocumentStatus, rider_id: uuid.UUID | None
) -> list[VerificationDocument]:
    statement = select(VerificationDocument).where(VerificationDocument.status == status)
    if rider_id:
        statement = statement.where(VerificationDocument.rider_id == rider_id)
    return list(db.scalars(statement.order_by(VerificationDocument.created_at).limit(100)))


def document_link(db: Session, document_id: uuid.UUID, admin: User) -> tuple[str, int]:
    """Every look at a rider's licence leaves a record of who looked."""
    document = db.get(VerificationDocument, document_id)
    if document is None:
        raise not_found("Document")
    ttl = get_settings().document_url_ttl_seconds
    url = storage.get_storage().presigned_url(document.storage_key, ttl)
    audit.record(db, admin.id, "document.viewed", "document", document.id)
    db.commit()
    return url, ttl


def _latest(db: Session, rider_id: uuid.UUID, document_type: DocumentType):
    return db.scalar(
        select(VerificationDocument)
        .where(
            VerificationDocument.rider_id == rider_id,
            VerificationDocument.document_type == document_type,
        )
        .order_by(VerificationDocument.created_at.desc())
        .limit(1)
    )


def review(
    db: Session, document_id: uuid.UUID, admin: User, approve: bool, note: str | None
) -> tuple[VerificationDocument, RiderProfile]:
    """The badge follows the latest document of each type, checked by a person."""
    document = db.get(VerificationDocument, document_id, with_for_update=True)
    if document is None:
        raise not_found("Document")
    if document.status != DocumentStatus.PENDING:
        raise conflict("ALREADY_REVIEWED", "This document has already been reviewed")

    document.status = DocumentStatus.APPROVED if approve else DocumentStatus.REJECTED
    document.review_note = note
    document.reviewed_by = admin.id
    document.reviewed_at = utcnow()
    audit.record(db, admin.id, "document.reviewed", "document", document.id, approved=approve)
    db.flush()

    rider = db.get(RiderProfile, document.rider_id)
    latest = [_latest(db, rider.user_id, kind) for kind in REQUIRED_DOCUMENTS]
    all_approved = all(d is not None and d.status == DocumentStatus.APPROVED for d in latest)
    checked = VerificationStatus.DOCUMENTS_MANUALLY_CHECKED
    if all_approved and rider.verification_status != checked:
        rider.verification_status = checked
    elif not all_approved and rider.verification_status == checked:
        rider.verification_status = VerificationStatus.PHONE_CONFIRMED
    rider.verification_checked_by = admin.id
    rider.verification_checked_at = utcnow()
    db.commit()
    return document, rider
