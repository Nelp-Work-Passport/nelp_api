"""Turns database rows into API responses. Privacy rules for output live here."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import schemas
from app.models import (
    Booking,
    BookingStatus,
    Payment,
    PaymentStatus,
    Rating,
    RiderProfile,
    Role,
    SmsType,
    User,
)
from app.security import mask_phone
from app.services import media, notifications, riders

CONTACT_STATUSES = (BookingStatus.ACCEPTED, BookingStatus.PICKED_UP)


def user_out(db: Session, user: User) -> schemas.UserOut:
    return schemas.UserOut(
        id=user.id,
        phone=user.phone,
        display_name=user.display_name,
        roles=sorted(user.roles),
        phone_confirmed=user.phone_confirmed_at is not None,
        profile_image_url=media.profile_url(db, user.id),
    )


def trust_out(stats: riders.RiderStats) -> schemas.TrustOut:
    trust = stats.trust
    return schemas.TrustOut(
        label=trust.label,
        is_new=trust.is_new,
        trust_score=trust.trust_score,
        average_stars=trust.average_stars,
        adjusted_rating=trust.adjusted_rating,
        rated_bookings=trust.rated_bookings,
        completed_deliveries=stats.completed_deliveries,
        last_rated_at=stats.last_rated_at,
        ratings_last_30_days=stats.ratings_last_30_days,
        tag_counts=stats.tag_counts,
    )


def _rider_fields(
    rider: RiderProfile, stats: riders.RiderStats, distance=None, image_url=None
) -> dict:
    return dict(
        id=rider.user_id,
        display_name=rider.display_name,
        vehicle_type=rider.vehicle_type,
        is_demo=rider.is_demo,
        trust=trust_out(stats),
        verification=schemas.VerificationOut(
            status=rider.verification_status,
            label=riders.VERIFICATION_LABELS[rider.verification_status],
        ),
        service_areas=[schemas.AreaOut.model_validate(a) for a in rider.service_areas],
        available_now=rider.is_available,
        availability_updated_at=rider.availability_updated_at,
        current_area=(
            schemas.AreaOut.model_validate(rider.current_area) if rider.current_area else None
        ),
        approx_distance_km=distance,
        profile_image_url=image_url,
    )


def rider_list_item(rider, stats, distance, image_url) -> schemas.RiderListItem:
    return schemas.RiderListItem(**_rider_fields(rider, stats, distance, image_url))


def rider_detail(db: Session, rider: RiderProfile) -> schemas.RiderDetail:
    stats = riders.stats_for(db, [rider.user_id])[rider.user_id]
    slots = [schemas.SlotOut.model_validate(s) for s in riders.upcoming_slots(db, rider.user_id)]
    image_url = media.profile_url(db, rider.user_id)
    return schemas.RiderDetail(**_rider_fields(rider, stats, None, image_url), upcoming_slots=slots)


def _contacts(booking: Booking, viewer: User) -> dict[str, str]:
    if booking.status not in CONTACT_STATUSES:
        return {}
    if viewer.id == booking.rider_id:
        return {"requester": booking.requester.phone, "recipient": booking.recipient_phone}
    if viewer.id in (booking.requester_id, booking.recipient_user_id):
        return {"rider": booking.rider.user.phone}
    return {}


def _can_rate(db: Session, booking: Booking, viewer: User) -> bool:
    if booking.status != BookingStatus.DELIVERED or viewer.id == booking.rider_id:
        return False
    if viewer.id not in (booking.requester_id, booking.recipient_user_id):
        return False
    return (
        db.scalar(
            select(Rating.id).where(Rating.booking_id == booking.id, Rating.author_id == viewer.id)
        )
        is None
    )


def _current_payment(booking: Booking) -> Payment | None:
    """The live or refunded payment if there is one, else the latest attempt."""
    wanted = (PaymentStatus.SUCCEEDED, PaymentStatus.PENDING, PaymentStatus.REFUNDED)
    for payment in reversed(booking.payments):
        if payment.status in wanted:
            return payment
    return booking.payments[-1] if booking.payments else None


def booking_out(db: Session, booking: Booking, viewer: User) -> schemas.BookingOut:
    db.expire(booking, ["payments", "history"])
    payment = _current_payment(booking)
    show_recipient = viewer.id == booking.requester_id or (
        viewer.id == booking.rider_id and booking.status in CONTACT_STATUSES
    )
    return schemas.BookingOut(
        id=booking.id,
        status=booking.status,
        rider=schemas.BookingRider(
            id=booking.rider_id,
            display_name=booking.rider.display_name,
            is_demo=booking.rider.is_demo,
            profile_image_url=media.profile_url(db, booking.rider_id),
        ),
        requester_name=booking.requester.display_name,
        pickup_area=schemas.AreaOut.model_validate(booking.pickup_area),
        dropoff_area=schemas.AreaOut.model_validate(booking.dropoff_area),
        scheduled_at=booking.scheduled_at,
        ends_at=booking.ends_at,
        fare_minor=booking.fare_minor,
        platform_fee_minor=booking.platform_fee_minor,
        currency=booking.currency,
        item_notes=booking.item_notes,
        tracking_url=notifications.tracking_url(booking),
        recipient_phone=(
            booking.recipient_phone if show_recipient else mask_phone(booking.recipient_phone)
        ),
        contact_phones=_contacts(booking, viewer),
        delivery_code_sms=notifications.latest_status(db, booking.id, SmsType.DELIVERY_CODE),
        cancellation_pending=(
            booking.status == BookingStatus.PICKED_UP
            and booking.cancellation_requested_at is not None
        ),
        cancellation_reason=booking.cancellation_reason,
        can_rate=_can_rate(db, booking, viewer),
        payment=(
            schemas.PaymentSummary(
                id=payment.id,
                status=payment.status,
                amount_minor=payment.amount_minor,
                currency=payment.currency,
                is_simulated=payment.is_simulated,
            )
            if payment
            else None
        ),
        history=[schemas.HistoryOut.model_validate(h) for h in booking.history],
        created_at=booking.created_at,
    )


def can_see_ledger(viewer: User, booking: Booking) -> bool:
    return Role.ADMIN in viewer.roles or viewer.id in (booking.requester_id, booking.rider_id)


def ledger_out(transactions) -> schemas.BookingLedgerOut:
    debits = credits = 0
    rows = []
    for transaction in transactions:
        entries = []
        for entry in transaction.entries:
            if entry.side == "DEBIT":
                debits += entry.amount_minor
            else:
                credits += entry.amount_minor
            entries.append(
                schemas.LedgerEntryOut(
                    account_type=entry.account.account_type,
                    owner_type=entry.account.owner_type,
                    side=entry.side,
                    amount_minor=entry.amount_minor,
                )
            )
        rows.append(
            schemas.LedgerTransactionOut(
                id=transaction.id,
                event_type=transaction.event_type,
                created_at=transaction.created_at,
                entries=entries,
            )
        )
    return schemas.BookingLedgerOut(
        transactions=rows,
        total_debits_minor=debits,
        total_credits_minor=credits,
        balanced=debits == credits,
    )
