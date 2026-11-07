import uuid
from typing import Annotated

from fastapi import APIRouter, Header
from pydantic import StringConstraints
from sqlalchemy import select

from app import presenters, schemas
from app.db import utcnow
from app.deps import Admin, Db
from app.errors import not_found
from app.models import (
    AuditEvent,
    Booking,
    BookingStatus,
    RatingReport,
    ReportStatus,
    RiderProfile,
    VerificationStatus,
)
from app.services import audit, bookings, flags, ledger, payments, ratings, riders

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/riders/{rider_id}/verification", response_model=schemas.VerificationOut)
def set_verification(rider_id: uuid.UUID, body: schemas.VerificationIn, admin: Admin, db: Db):
    """Records who checked. Use the documents status only after a real check."""
    rider = db.get(RiderProfile, rider_id)
    if rider is None:
        raise not_found("Rider")
    rider.verification_status = VerificationStatus(body.status)
    rider.verification_checked_by = admin.id
    rider.verification_checked_at = utcnow()
    audit.record(
        db,
        admin.id,
        "rider.verification_set",
        "rider",
        rider_id,
        status=body.status,
        note=body.note,
    )
    db.commit()
    return schemas.VerificationOut(
        status=rider.verification_status,
        label=riders.VERIFICATION_LABELS[rider.verification_status],
    )


@router.get("/cancellations", response_model=list[schemas.BookingOut])
def pending_cancellations(admin: Admin, db: Db):
    found = db.scalars(
        select(Booking).where(
            Booking.status == BookingStatus.PICKED_UP,
            Booking.cancellation_requested_at.is_not(None),
        )
    ).all()
    return [presenters.booking_out(db, booking, admin) for booking in found]


@router.post("/bookings/{booking_id}/resolve-cancellation", response_model=schemas.BookingOut)
def resolve_cancellation(
    booking_id: uuid.UUID, body: schemas.ResolveCancellationIn, admin: Admin, db: Db
):
    booking = bookings.resolve_cancellation(db, booking_id, admin, body.approve, body.note)
    return presenters.booking_out(db, booking, admin)


@router.get("/rating-reports", response_model=list[schemas.ReportOut])
def open_reports(admin: Admin, db: Db):
    return db.scalars(
        select(RatingReport)
        .where(RatingReport.status == ReportStatus.OPEN)
        .order_by(RatingReport.created_at)
    ).all()


@router.post("/rating-reports/{report_id}/resolve", response_model=schemas.ReportOut)
def resolve_report(report_id: uuid.UUID, body: schemas.ResolveReportIn, admin: Admin, db: Db):
    return ratings.resolve_report(db, report_id, admin, body.uphold)


@router.get("/flags", response_model=list[schemas.FlagOut])
def review_flags(admin: Admin, db: Db):
    """Prompts for human review. They never change a score or hide a rider."""
    return flags.suspicious_patterns(db)


@router.get("/ledger/trial-balance", response_model=schemas.TrialBalanceOut)
def trial_balance(admin: Admin, db: Db):
    rows = ledger.trial_balance(db)
    debits = sum(row["debits_minor"] for row in rows)
    credits = sum(row["credits_minor"] for row in rows)
    return schemas.TrialBalanceOut(
        accounts=rows,
        total_debits_minor=debits,
        total_credits_minor=credits,
        balanced=debits == credits,
    )


@router.post("/riders/{rider_id}/payouts", response_model=schemas.PayoutOut)
def pay_out_rider(
    rider_id: uuid.UUID,
    body: schemas.PayoutIn,
    admin: Admin,
    db: Db,
    idempotency_key: Annotated[
        Annotated[str, StringConstraints(min_length=8, max_length=100)], Header()
    ],
):
    if db.get(RiderProfile, rider_id) is None:
        raise not_found("Rider")
    return payments.pay_out_rider(db, admin, rider_id, body.amount_minor, idempotency_key)


@router.get("/audit-events", response_model=list[schemas.AuditOut])
def audit_events(admin: Admin, db: Db, entity_id: str | None = None):
    statement = select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(100)
    if entity_id:
        statement = statement.where(AuditEvent.entity_id == entity_id)
    return db.scalars(statement).all()
