"""The booking state machine.

REQUESTED -> ACCEPTED -> PICKED_UP -> DELIVERED
REQUESTED -> DECLINED (rider)
REQUESTED or ACCEPTED -> CANCELLED (either side)
PICKED_UP -> CANCELLED only after a recorded reason and support review
DELIVERED only through a valid recipient code
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import utcnow
from app.errors import AppError, conflict, forbidden, not_found
from app.models import (
    Booking,
    BookingStatus,
    BookingStatusHistory,
    DeliveryConfirmation,
    RiderProfile,
    Role,
    ServiceArea,
    SmsStatus,
    SmsType,
    User,
)
from app.rate_limit import limiter
from app.security import code_matches, generate_code, hash_code
from app.services import audit, fares, notifications, payments, riders

CODE_PURPOSE = "delivery"
PAST_TOLERANCE = timedelta(minutes=5)


def _set_status(
    db: Session,
    booking: Booking,
    new_status: BookingStatus,
    actor_id: uuid.UUID | None,
    reason: str | None = None,
) -> None:
    db.add(
        BookingStatusHistory(
            booking_id=booking.id,
            old_status=booking.status,
            new_status=new_status,
            changed_by=actor_id,
            reason=reason,
        )
    )
    audit.record(
        db,
        actor_id,
        "booking.status_changed",
        "booking",
        booking.id,
        old=booking.status,
        new=new_status,
    )
    booking.status = new_status


def _load(db: Session, booking_id: uuid.UUID, lock: bool = False) -> Booking:
    booking = db.get(Booking, booking_id, with_for_update=lock, populate_existing=lock)
    if booking is None:
        raise not_found("Booking")
    return booking


def can_view(user: User, booking: Booking) -> bool:
    return Role.ADMIN in user.roles or user.id in (
        booking.requester_id,
        booking.rider_id,
        booking.recipient_user_id,
    )


def get_for_user(db: Session, booking_id: uuid.UUID, user: User) -> Booking:
    booking = _load(db, booking_id)
    if not can_view(user, booking):
        raise not_found("Booking")  # do not reveal that the booking exists
    return booking


def _require_rider(booking: Booking, user: User) -> None:
    if booking.rider_id != user.id:
        raise forbidden("Only the selected rider can do this")


def _require_status(booking: Booking, *allowed: BookingStatus) -> None:
    if booking.status not in allowed:
        raise conflict("INVALID_TRANSITION", f"This cannot be done to a {booking.status} booking")


def create(
    db: Session,
    requester: User,
    *,
    rider_id: uuid.UUID,
    pickup_area_id: uuid.UUID,
    dropoff_area_id: uuid.UUID,
    scheduled_at: datetime | None,
    recipient_phone: str | None,
    item_notes: str | None,
) -> Booking:
    if requester.phone_confirmed_at is None:
        raise forbidden("Confirm your phone number before booking")
    if rider_id == requester.id:
        raise AppError(422, "SELF_BOOKING", "You cannot book yourself")

    now = utcnow()
    settings = get_settings()
    if scheduled_at is not None:
        if scheduled_at < now - PAST_TOLERANCE:
            raise AppError(422, "TIME_IN_PAST", "Choose a time in the future")
        if scheduled_at > now + timedelta(days=settings.max_days_ahead):
            raise AppError(
                422, "TIME_TOO_FAR", f"Book at most {settings.max_days_ahead} days ahead"
            )

    pickup = db.get(ServiceArea, pickup_area_id)
    dropoff = db.get(ServiceArea, dropoff_area_id)
    if pickup is None or dropoff is None:
        raise not_found("Service area")

    rider = riders.get_listed_rider(db, rider_id)
    if not riders.can_take_booking(db, rider, pickup_area_id, scheduled_at):
        raise conflict("RIDER_UNAVAILABLE", "This rider is not available for that area and time")

    start, end = riders.booking_window(scheduled_at or now)
    quote = fares.quote(pickup, dropoff)
    phone = recipient_phone or requester.phone
    recipient = db.scalar(select(User).where(User.phone == phone))
    booking = Booking(
        requester_id=requester.id,
        recipient_user_id=recipient.id if recipient else None,
        recipient_phone=phone,
        rider_id=rider_id,
        pickup_area_id=pickup_area_id,
        dropoff_area_id=dropoff_area_id,
        scheduled_at=start,
        ends_at=end,
        fare_minor=quote.fare_minor,
        platform_fee_minor=quote.platform_fee_minor,
        currency=quote.currency,
        item_notes=item_notes,
    )
    db.add(booking)
    db.flush()
    db.add(
        BookingStatusHistory(
            booking_id=booking.id,
            new_status=BookingStatus.REQUESTED,
            changed_by=requester.id,
        )
    )
    audit.record(db, requester.id, "booking.created", "booking", booking.id)
    db.commit()

    notifications.send(
        db,
        rider.user.phone,
        f"Nelp: New delivery request for {notifications.nairobi_time(start)}. "
        "Open Nelp to accept or decline.",
        SmsType.BOOKING_REQUESTED,
        booking.id,
        rider.user_id,
    )
    return booking


def accept(db: Session, booking_id: uuid.UUID, rider_user: User) -> Booking:
    booking = _load(db, booking_id, lock=True)
    _require_rider(booking, rider_user)
    _require_status(booking, BookingStatus.REQUESTED)

    # Holding the rider row serialises accepts, so two overlapping bookings
    # cannot both pass the busy check.
    db.get(RiderProfile, booking.rider_id, with_for_update=True, populate_existing=True)
    if riders.rider_is_busy(db, booking.rider_id, booking.scheduled_at, booking.ends_at):
        raise conflict("SLOT_TAKEN", "You already have a booking at this time")

    _set_status(db, booking, BookingStatus.ACCEPTED, rider_user.id)
    code = generate_code()
    expires_at = max(booking.scheduled_at, utcnow()) + timedelta(
        hours=get_settings().code_ttl_hours
    )
    db.add(
        DeliveryConfirmation(
            booking_id=booking.id,
            code_hash=hash_code(CODE_PURPOSE, booking.id, code),
            expires_at=expires_at,
        )
    )
    db.commit()
    db.refresh(booking, ["confirmation"])
    _send_code(db, booking, code)
    if booking.recipient_phone != booking.requester.phone:
        notifications.send(
            db,
            booking.requester.phone,
            f"Nelp: {booking.rider.display_name} accepted your delivery for "
            f"{notifications.nairobi_time(booking.scheduled_at)}.",
            SmsType.BOOKING_ACCEPTED,
            booking.id,
            booking.requester_id,
        )
    return booking


def _send_code(db: Session, booking: Booking, code: str) -> None:
    message = notifications.send(
        db,
        booking.recipient_phone,
        notifications.delivery_code_text(booking, code),
        SmsType.DELIVERY_CODE,
        booking.id,
        booking.recipient_user_id,
    )
    if message.status == SmsStatus.SENT:
        booking.confirmation.sent_at = message.sent_at
        db.commit()


def resend_code(db: Session, booking_id: uuid.UUID, user: User) -> Booking:
    booking = get_for_user(db, booking_id, user)
    limiter.hit(f"resend-code:{booking.id}", 3, 60)
    booking = _load(db, booking_id, lock=True)
    _require_status(booking, BookingStatus.ACCEPTED, BookingStatus.PICKED_UP)
    confirmation = booking.confirmation
    settings = get_settings()
    if confirmation.resend_count >= settings.max_code_resends:
        raise AppError(429, "RESEND_LIMIT", "This code cannot be resent again. Contact support.")

    code = generate_code()
    confirmation.code_hash = hash_code(CODE_PURPOSE, booking.id, code)
    confirmation.resend_count += 1
    confirmation.expires_at = max(booking.scheduled_at, utcnow()) + timedelta(
        hours=settings.code_ttl_hours
    )
    db.commit()
    _send_code(db, booking, code)
    return booking


def decline(db: Session, booking_id: uuid.UUID, rider_user: User, reason: str | None) -> Booking:
    booking = _load(db, booking_id, lock=True)
    _require_rider(booking, rider_user)
    _require_status(booking, BookingStatus.REQUESTED)
    _set_status(db, booking, BookingStatus.DECLINED, rider_user.id, reason)
    payments.refund_booking(db, booking, rider_user.id)
    db.commit()
    notifications.send(
        db,
        booking.requester.phone,
        "Nelp: Your rider could not take this delivery. Open Nelp to choose another rider.",
        SmsType.BOOKING_DECLINED,
        booking.id,
        booking.requester_id,
    )
    return booking


def mark_picked_up(db: Session, booking_id: uuid.UUID, rider_user: User) -> Booking:
    booking = _load(db, booking_id, lock=True)
    _require_rider(booking, rider_user)
    _require_status(booking, BookingStatus.ACCEPTED)
    _set_status(db, booking, BookingStatus.PICKED_UP, rider_user.id)
    db.commit()
    return booking


def cancel(db: Session, booking_id: uuid.UUID, user: User, reason: str | None) -> Booking:
    booking = _load(db, booking_id, lock=True)
    if user.id not in (booking.requester_id, booking.rider_id):
        raise not_found("Booking")
    _require_status(
        booking, BookingStatus.REQUESTED, BookingStatus.ACCEPTED, BookingStatus.PICKED_UP
    )

    if booking.status == BookingStatus.PICKED_UP:
        if not reason:
            raise AppError(422, "REASON_REQUIRED", "Give a reason to cancel after pickup")
        if booking.cancellation_requested_at:
            raise conflict("CANCELLATION_PENDING", "A cancellation is already under review")
        booking.cancellation_reason = reason
        booking.cancellation_requested_by = user.id
        booking.cancellation_requested_at = utcnow()
        audit.record(db, user.id, "booking.cancellation_requested", "booking", booking.id)
        db.commit()
        return booking

    booking.cancellation_reason = reason
    _set_status(db, booking, BookingStatus.CANCELLED, user.id, reason)
    payments.refund_booking(db, booking, user.id)
    db.commit()
    other = booking.rider.user if user.id == booking.requester_id else booking.requester
    notifications.send(
        db,
        other.phone,
        f"Nelp: The delivery for {notifications.nairobi_time(booking.scheduled_at)} was cancelled.",
        SmsType.BOOKING_CANCELLED,
        booking.id,
        other.id,
    )
    return booking


def resolve_cancellation(
    db: Session, booking_id: uuid.UUID, admin: User, approve: bool, note: str | None
) -> Booking:
    booking = _load(db, booking_id, lock=True)
    if booking.status != BookingStatus.PICKED_UP or not booking.cancellation_requested_at:
        raise conflict("NO_PENDING_CANCELLATION", "No cancellation is waiting for review")
    audit.record(
        db,
        admin.id,
        "booking.cancellation_reviewed",
        "booking",
        booking.id,
        approved=approve,
        note=note,
    )
    if approve:
        _set_status(db, booking, BookingStatus.CANCELLED, admin.id, booking.cancellation_reason)
        payments.refund_booking(db, booking, admin.id)
    else:
        booking.cancellation_requested_at = None
        booking.cancellation_requested_by = None
        booking.cancellation_reason = None
    db.commit()
    return booking


def confirm_delivery(db: Session, booking_id: uuid.UUID, user: User, code: str) -> Booking:
    booking = _load(db, booking_id, lock=True)
    if user.id not in (booking.requester_id, booking.rider_id, booking.recipient_user_id):
        raise not_found("Booking")
    _require_status(booking, BookingStatus.PICKED_UP)

    confirmation = booking.confirmation
    settings = get_settings()
    if confirmation.attempts >= settings.max_code_attempts:
        raise AppError(429, "CODE_LOCKED", "Too many wrong codes. Contact support.")
    if confirmation.expires_at <= utcnow():
        raise AppError(410, "CODE_EXPIRED", "This code has expired. Request a new one.")
    if not code_matches(confirmation.code_hash, CODE_PURPOSE, booking.id, code):
        confirmation.attempts += 1
        remaining = settings.max_code_attempts - confirmation.attempts
        db.commit()
        raise AppError(400, "WRONG_CODE", f"Wrong code. {remaining} attempts left.")

    confirmation.confirmed_at = utcnow()
    # A valid code proves the delivery happened, so it settles any open request.
    booking.cancellation_requested_at = None
    booking.cancellation_requested_by = None
    _set_status(db, booking, BookingStatus.DELIVERED, user.id)
    payments.allocate_if_ready(db, booking)
    db.commit()
    return booking
