"""Rating eligibility, reports and the review queue."""

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import utcnow
from app.errors import conflict, forbidden, not_found
from app.models import (
    Booking,
    BookingStatus,
    Rating,
    RatingReport,
    RatingStatus,
    ReportStatus,
    Role,
    User,
)
from app.rate_limit import limiter
from app.services import audit


def rate_booking(
    db: Session,
    booking_id: uuid.UUID,
    author: User,
    stars: int,
    tags: list[str],
    comment: str | None,
) -> Rating:
    limiter.hit(f"rating:{author.id}", 20, 3600)
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise not_found("Booking")
    eligible_authors = {booking.requester_id, booking.recipient_user_id} - {booking.rider_id}
    if author.id not in eligible_authors:
        raise forbidden("Only the customer, merchant or recipient of this booking can rate it")

    confirmed = booking.confirmation and booking.confirmation.confirmed_at
    if booking.status != BookingStatus.DELIVERED or not confirmed:
        raise conflict("NOT_RATEABLE", "Only a delivered booking can be rated")

    rating = Rating(
        booking_id=booking.id, author_id=author.id, stars=stars, tags=tags, comment=comment
    )
    db.add(rating)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise conflict("ALREADY_RATED", "You have already rated this booking") from exc
    audit.record(db, author.id, "rating.created", "rating", rating.id, booking_id=str(booking.id))
    db.commit()
    return rating


def report(db: Session, rating_id: uuid.UUID, reporter: User, reason: str) -> RatingReport:
    rating = db.get(Rating, rating_id)
    if rating is None:
        raise not_found("Rating")
    booking = rating.booking
    involved = {booking.rider_id, booking.requester_id, booking.recipient_user_id}
    if reporter.id not in involved and Role.ADMIN not in reporter.roles:
        raise not_found("Rating")

    item = RatingReport(rating_id=rating.id, reporter_id=reporter.id, reason=reason)
    db.add(item)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise conflict("ALREADY_REPORTED", "You have already reported this rating") from exc
    audit.record(db, reporter.id, "rating.reported", "rating", rating.id)
    db.commit()
    return item


def resolve_report(db: Session, report_id: uuid.UUID, admin: User, uphold: bool) -> RatingReport:
    """Only a person removes a rating. Automated flags never do."""
    item = db.get(RatingReport, report_id)
    if item is None:
        raise not_found("Report")
    if item.status != ReportStatus.OPEN:
        raise conflict("ALREADY_REVIEWED", "This report has already been reviewed")
    item.status = ReportStatus.UPHELD if uphold else ReportStatus.DISMISSED
    item.reviewed_by = admin.id
    item.reviewed_at = utcnow()
    if uphold:
        item.rating.status = RatingStatus.REMOVED
    audit.record(db, admin.id, "rating.report_reviewed", "rating", item.rating_id, upheld=uphold)
    db.commit()
    return item
