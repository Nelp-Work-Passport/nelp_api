"""Ratings reports, payments, public tracking, SMS reports and the demo inbox."""

import hmac
import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from pydantic import StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import schemas
from app.adapters.payments import SimulatedGateway, get_payment_gateway
from app.config import get_settings
from app.db import utcnow
from app.deps import CurrentUser, Db, Requester
from app.errors import AppError, not_found
from app.models import (
    Booking,
    DemoInboxMessage,
    Payment,
    SmsMessage,
    SmsStatus,
)
from app.security import normalize_phone
from app.services import payments, ratings

router = APIRouter()
IdempotencyKey = Annotated[str, StringConstraints(min_length=8, max_length=120)]


async def _raw_body(request: Request) -> bytes:
    return await request.body()


@router.post(
    "/api/ratings/{rating_id}/report",
    response_model=schemas.ReportOut,
    status_code=201,
    tags=["ratings"],
)
def report_rating(rating_id: uuid.UUID, body: schemas.ReportIn, user: CurrentUser, db: Db):
    return ratings.report(db, rating_id, user, body.reason)


@router.post("/api/payments/intent", response_model=schemas.PaymentOut, tags=["payments"])
def payment_intent(
    body: schemas.PaymentIntentIn,
    user: Requester,
    db: Db,
    idempotency_key: Annotated[IdempotencyKey, Header()],
):
    """Sending the same Idempotency-Key again returns the same payment."""
    return payments.create_intent(db, user, body.booking_id, idempotency_key)


@router.post("/api/payments/webhook", response_model=schemas.PaymentOut, tags=["payments"])
def payment_webhook(
    db: Db,
    body: Annotated[bytes, Depends(_raw_body)],
    x_nelp_signature: Annotated[str | None, Header()] = None,
):
    event = get_payment_gateway().parse_webhook(body, x_nelp_signature)
    try:
        return payments.apply_provider_event(db, event)
    except IntegrityError:  # a concurrent duplicate won the race and posted first
        db.rollback()
        return db.scalar(
            select(Payment).where(Payment.provider_reference == event.provider_reference)
        )


@router.post(
    "/api/payments/{payment_id}/simulate", response_model=schemas.PaymentOut, tags=["payments"]
)
def simulate_payment(payment_id: uuid.UUID, body: schemas.SimulateIn, user: CurrentUser, db: Db):
    """Plays the provider's part. It runs the same code path as a real webhook."""
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise not_found("Payment")
    payments.can_simulate(user, payment)
    gateway = SimulatedGateway()
    raw = json.dumps(
        {
            "provider_reference": payment.provider_reference,
            "status": body.outcome,
            "amount_minor": payment.amount_minor,
            "currency": payment.currency,
        }
    ).encode()
    return payments.apply_provider_event(db, gateway.parse_webhook(raw, gateway.sign(raw)))


@router.get("/api/track/{token}", response_model=schemas.TrackingOut, tags=["bookings"])
def track(token: str, db: Db):
    """The link in the SMS. It works without login, so it shows no private detail."""
    booking = db.scalar(select(Booking).where(Booking.tracking_token == token))
    if booking is None:
        raise not_found("Booking")
    return schemas.TrackingOut(
        status=booking.status,
        rider_name=booking.rider.display_name,
        pickup_area=booking.pickup_area.name,
        dropoff_area=booking.dropoff_area.name,
        scheduled_at=booking.scheduled_at,
        updated_at=booking.updated_at,
    )


@router.post("/api/sms/dlr", tags=["sms"])
def sms_delivery_report(
    body: schemas.SmsDlrIn,
    db: Db,
    x_dlr_secret: Annotated[str | None, Header()] = None,
):
    """Replace this body shape once ThriveVas confirms its delivery report format."""
    if not x_dlr_secret or not hmac.compare_digest(x_dlr_secret, get_settings().sms_dlr_secret):
        raise AppError(401, "BAD_SECRET", "Invalid delivery report secret")
    message = db.scalar(
        select(SmsMessage).where(SmsMessage.provider_reference == body.provider_reference)
    )
    if message is None:
        raise not_found("SMS")
    message.status = SmsStatus(body.status)
    if message.status == SmsStatus.DELIVERED:
        message.delivered_at = utcnow()
    db.commit()
    return {"status": message.status}


@router.get("/api/demo/inbox", response_model=list[schemas.InboxMessage], tags=["demo"])
def demo_inbox(phone: str, db: Db):
    """The labelled SMS fallback. It does not exist once a live provider is on."""
    if get_settings().sms_provider != "demo":
        raise not_found("Page")
    try:
        normalized = normalize_phone(phone)
    except ValueError as exc:
        raise AppError(422, "BAD_PHONE", str(exc)) from exc
    return db.scalars(
        select(DemoInboxMessage)
        .where(DemoInboxMessage.phone == normalized)
        .order_by(DemoInboxMessage.created_at.desc())
        .limit(20)
    ).all()
