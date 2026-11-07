import uuid
from typing import Literal

from fastapi import APIRouter, Query
from sqlalchemy import or_, select

from app import presenters, schemas
from app.deps import CurrentUser, Db, Requester, Rider
from app.errors import not_found
from app.models import Booking, BookingStatus
from app.rate_limit import limiter
from app.services import bookings, ledger, ratings, riders

router = APIRouter(prefix="/api/bookings", tags=["bookings"])


@router.post("", response_model=schemas.BookingOut, status_code=201)
def create_booking(body: schemas.BookingIn, user: Requester, db: Db):
    limiter.hit(f"booking:{user.id}", 20, 3600)
    booking = bookings.create(db, user, **body.model_dump())
    return presenters.booking_out(db, booking, user)


@router.get("", response_model=list[schemas.BookingOut])
def my_bookings(
    user: CurrentUser,
    db: Db,
    role: Literal["all", "requester", "rider"] = "all",
    status: BookingStatus | None = None,
    limit: int = Query(default=50, ge=1, le=100),
):
    filters = {
        "all": or_(
            Booking.requester_id == user.id,
            Booking.rider_id == user.id,
            Booking.recipient_user_id == user.id,
        ),
        "requester": Booking.requester_id == user.id,
        "rider": Booking.rider_id == user.id,
    }
    statement = select(Booking).where(filters[role])
    if status:
        statement = statement.where(Booking.status == status)
    found = db.scalars(statement.order_by(Booking.scheduled_at.desc()).limit(limit)).all()
    return [presenters.booking_out(db, booking, user) for booking in found]


@router.get("/{booking_id}", response_model=schemas.BookingOut)
def get_booking(booking_id: uuid.UUID, user: CurrentUser, db: Db):
    return presenters.booking_out(db, bookings.get_for_user(db, booking_id, user), user)


@router.post("/{booking_id}/accept", response_model=schemas.BookingOut)
def accept_booking(booking_id: uuid.UUID, user: Rider, db: Db):
    return presenters.booking_out(db, bookings.accept(db, booking_id, user), user)


@router.post("/{booking_id}/decline", response_model=schemas.BookingOut)
def decline_booking(booking_id: uuid.UUID, body: schemas.ReasonIn, user: Rider, db: Db):
    return presenters.booking_out(db, bookings.decline(db, booking_id, user, body.reason), user)


@router.post("/{booking_id}/status", response_model=schemas.BookingOut)
def update_status(booking_id: uuid.UUID, body: schemas.StatusIn, user: Rider, db: Db):
    """DELIVERED is not accepted here. Only confirm-delivery can set it."""
    return presenters.booking_out(db, bookings.mark_picked_up(db, booking_id, user), user)


@router.post("/{booking_id}/cancel", response_model=schemas.BookingOut)
def cancel_booking(booking_id: uuid.UUID, body: schemas.ReasonIn, user: CurrentUser, db: Db):
    """After pickup this needs a reason and waits for support review."""
    return presenters.booking_out(db, bookings.cancel(db, booking_id, user, body.reason), user)


@router.post("/{booking_id}/confirm-delivery", response_model=schemas.BookingOut)
def confirm_delivery(
    booking_id: uuid.UUID, body: schemas.ConfirmDeliveryIn, user: CurrentUser, db: Db
):
    booking = bookings.confirm_delivery(db, booking_id, user, body.code)
    return presenters.booking_out(db, booking, user)


@router.post("/{booking_id}/resend-code", response_model=schemas.BookingOut)
def resend_delivery_code(booking_id: uuid.UUID, user: CurrentUser, db: Db):
    return presenters.booking_out(db, bookings.resend_code(db, booking_id, user), user)


@router.post("/{booking_id}/ratings", response_model=schemas.RatingOut, status_code=201)
def rate_booking(booking_id: uuid.UUID, body: schemas.RatingIn, user: CurrentUser, db: Db):
    rating = ratings.rate_booking(db, booking_id, user, body.stars, body.tags, body.comment)
    rider_id = rating.booking.rider_id
    return schemas.RatingOut(
        id=rating.id,
        booking_id=rating.booking_id,
        stars=rating.stars,
        tags=rating.tags,
        rider_trust=presenters.trust_out(riders.stats_for(db, [rider_id])[rider_id]),
    )


@router.get("/{booking_id}/ledger", response_model=schemas.BookingLedgerOut)
def booking_ledger(booking_id: uuid.UUID, user: CurrentUser, db: Db):
    booking = bookings.get_for_user(db, booking_id, user)
    if not presenters.can_see_ledger(user, booking):
        raise not_found("Booking")
    return presenters.ledger_out(ledger.booking_transactions(db, booking_id))
