"""Profile images and verification documents."""

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile

from app import schemas
from app.config import get_settings
from app.deps import Admin, CurrentUser, Db, Rider
from app.models import DocumentStatus, DocumentType
from app.services import media, riders

router = APIRouter(tags=["files"])
Upload = Annotated[UploadFile, File(description="Max 5 MB")]


def _read(file: UploadFile) -> bytes:
    """Reads one byte past the limit, which is enough to reject an oversized file."""
    return file.file.read(get_settings().max_upload_bytes + 1)


@router.put("/api/users/me/profile-image", response_model=schemas.ProfileImageOut)
def upload_profile_image(file: Upload, user: CurrentUser, db: Db):
    """JPEG, PNG or WebP. Stored as a resized JPEG with photo metadata removed."""
    return schemas.ProfileImageOut(
        profile_image_url=media.save_profile_image(db, user, _read(file)),
        expires_in_seconds=get_settings().profile_image_url_ttl_seconds,
    )


@router.delete("/api/users/me/profile-image", status_code=204)
def remove_profile_image(user: CurrentUser, db: Db):
    media.delete_profile_image(db, user)


@router.post(
    "/api/riders/me/verification-documents",
    response_model=schemas.DocumentOut,
    status_code=201,
)
def upload_document(
    document_type: Annotated[DocumentType, Form()], file: Upload, user: Rider, db: Db
):
    """JPEG, PNG, WebP or PDF. Uploading again replaces the document under review."""
    return media.upload_document(db, user, document_type, _read(file))


@router.get("/api/riders/me/verification-documents", response_model=list[schemas.DocumentOut])
def my_documents(user: Rider, db: Db):
    return media.my_documents(db, user)


@router.get(
    "/api/admin/verification-documents",
    response_model=list[schemas.AdminDocumentOut],
    tags=["admin"],
)
def documents_for_review(
    admin: Admin,
    db: Db,
    status: DocumentStatus = DocumentStatus.PENDING,
    rider_id: uuid.UUID | None = None,
):
    return media.list_for_review(db, status, rider_id)


@router.get(
    "/api/admin/verification-documents/{document_id}/link",
    response_model=schemas.DocumentLinkOut,
    tags=["admin"],
)
def document_link(document_id: uuid.UUID, admin: Admin, db: Db):
    """A link that works for five minutes. Each request is recorded in the audit log."""
    url, ttl = media.document_link(db, document_id, admin)
    return schemas.DocumentLinkOut(url=url, expires_in_seconds=ttl)


@router.post(
    "/api/admin/verification-documents/{document_id}/review",
    response_model=schemas.DocumentReviewOut,
    tags=["admin"],
)
def review_document(document_id: uuid.UUID, body: schemas.DocumentReviewIn, admin: Admin, db: Db):
    """The badge is set once the latest rider card and licence are both approved."""
    document, rider = media.review(db, document_id, admin, body.approve, body.note)
    return schemas.DocumentReviewOut(
        document=schemas.AdminDocumentOut.model_validate(document),
        verification=schemas.VerificationOut(
            status=rider.verification_status,
            label=riders.VERIFICATION_LABELS[rider.verification_status],
        ),
    )
