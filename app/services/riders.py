"""Rider search, availability and reputation statistics."""

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.db import utcnow
from app.errors import AppError, not_found
from app.models import (
    AvailabilitySlot,
    Booking,
    BookingStatus,
    DeliveryConfirmation,
    Rating,
    RatingStatus,
    RiderProfile,
    RiderServiceArea,
    ServiceArea,
    SlotStatus,
    User,
    VerificationStatus,
)
from app.services import fares, scoring

BUSY_STATUSES = (BookingStatus.ACCEPTED, BookingStatus.PICKED_UP)
RECENT_DAYS = 30

VERIFICATION_LABELS = {
    VerificationStatus.UNVERIFIED: "Phone not confirmed",
    VerificationStatus.PHONE_CONFIRMED: "Phone confirmed",
    VerificationStatus.DOCUMENTS_MANUALLY_CHECKED: "Documents manually checked",
}


@dataclass
class RiderStats:
    trust: scoring.TrustSummary
    completed_deliveries: int = 0
    last_rated_at: datetime | None = None
    ratings_last_30_days: int = 0
    tag_counts: dict[str, int] = field(default_factory=dict)


def stats_for(db: Session, rider_ids: list[uuid.UUID]) -> dict[uuid.UUID, RiderStats]:
    """Reputation is always derived from eligible ratings, never stored."""
    if not rider_ids:
        return {}

    completed = dict(
        db.execute(
            select(Booking.rider_id, func.count())
            .where(Booking.rider_id.in_(rider_ids), Booking.status == BookingStatus.DELIVERED)
            .group_by(Booking.rider_id)
        ).all()
    )
    rows = db.execute(
        select(Booking.rider_id, Booking.id, Rating.stars, Rating.tags, Rating.created_at)
        .join(Rating, Rating.booking_id == Booking.id)
        .join(DeliveryConfirmation, DeliveryConfirmation.booking_id == Booking.id)
        .where(
            Booking.rider_id.in_(rider_ids),
            Booking.status == BookingStatus.DELIVERED,
            DeliveryConfirmation.confirmed_at.is_not(None),
            Rating.status == RatingStatus.ACTIVE,
        )
    ).all()

    stars_by_booking: dict[uuid.UUID, dict[uuid.UUID, list[int]]] = defaultdict(
        lambda: defaultdict(list)
    )
    tags: dict[uuid.UUID, Counter] = defaultdict(Counter)
    last_rated: dict[uuid.UUID, datetime] = {}
    recent: Counter = Counter()
    cutoff = utcnow() - timedelta(days=RECENT_DAYS)

    for rider_id, booking_id, stars, rating_tags, created_at in rows:
        stars_by_booking[rider_id][booking_id].append(stars)
        tags[rider_id].update(rating_tags or [])
        if rider_id not in last_rated or created_at > last_rated[rider_id]:
            last_rated[rider_id] = created_at
        if created_at >= cutoff:
            recent[rider_id] += 1

    result = {}
    for rider_id in rider_ids:
        values = [scoring.booking_value(s) for s in stars_by_booking[rider_id].values()]
        result[rider_id] = RiderStats(
            trust=scoring.compute_trust(values),
            completed_deliveries=completed.get(rider_id, 0),
            last_rated_at=last_rated.get(rider_id),
            ratings_last_30_days=recent[rider_id],
            tag_counts=dict(tags[rider_id]),
        )
    return result


def booking_window(start: datetime) -> tuple[datetime, datetime]:
    return start, start + timedelta(minutes=get_settings().booking_duration_minutes)


def rider_is_busy(db: Session, rider_id: uuid.UUID, start: datetime, end: datetime) -> bool:
    return bool(
        db.scalar(
            select(
                exists().where(
                    Booking.rider_id == rider_id,
                    Booking.status.in_(BUSY_STATUSES),
                    Booking.scheduled_at < end,
                    Booking.ends_at > start,
                )
            )
        )
    )


def _listed_riders():
    """Only riders with a confirmed phone and an active account can be chosen."""
    return (
        select(RiderProfile)
        .join(User, User.id == RiderProfile.user_id)
        .where(User.is_active, User.phone_confirmed_at.is_not(None))
        .options(selectinload(RiderProfile.current_area))
    )


def _available_filter(area_id: uuid.UUID | None, at: datetime | None):
    """A rider is free now via the toggle, or at a future time via an open slot."""
    start, end = booking_window(at or utcnow())
    conditions = [
        ~exists().where(
            Booking.rider_id == RiderProfile.user_id,
            Booking.status.in_(BUSY_STATUSES),
            Booking.scheduled_at < end,
            Booking.ends_at > start,
        )
    ]
    if at is None:
        conditions.append(RiderProfile.is_available)
    else:
        slot_conditions = [
            AvailabilitySlot.rider_id == RiderProfile.user_id,
            AvailabilitySlot.status == SlotStatus.OPEN,
            AvailabilitySlot.starts_at <= start,
            AvailabilitySlot.ends_at >= end,
        ]
        if area_id:
            slot_conditions.append(AvailabilitySlot.service_area_id == area_id)
        conditions.append(exists().where(*slot_conditions))
    return conditions


def search(
    db: Session,
    area_id: uuid.UUID | None,
    at: datetime | None,
    sort: str,
    include_demo: bool = True,
) -> tuple[list[RiderProfile], dict, dict]:
    statement = _listed_riders().where(*_available_filter(area_id, at))
    area = None
    if area_id:
        area = db.get(ServiceArea, area_id)
        if area is None:
            raise not_found("Service area")
        statement = statement.join(
            RiderServiceArea, RiderServiceArea.rider_id == RiderProfile.user_id
        ).where(RiderServiceArea.service_area_id == area_id)
    if not include_demo:
        statement = statement.where(RiderProfile.is_demo.is_(False))

    riders = list(db.scalars(statement).unique())
    stats = stats_for(db, [r.user_id for r in riders])
    distances = {
        r.user_id: fares.distance_km(area, r.current_area) if area and r.current_area else None
        for r in riders
    }

    if sort == "distance":
        riders.sort(key=lambda r: (distances[r.user_id] is None, distances[r.user_id] or 0))
    elif sort == "availability":
        riders.sort(
            key=lambda r: (
                not r.is_available,
                -(r.availability_updated_at.timestamp() if r.availability_updated_at else 0),
            )
        )
    else:
        # New riders follow the riders with a conclusive score. They stay listed.
        riders.sort(
            key=lambda r: (
                stats[r.user_id].trust.is_new,
                -(stats[r.user_id].trust.trust_score or 0),
                -stats[r.user_id].completed_deliveries,
            )
        )
    return riders, stats, distances


def get_listed_rider(db: Session, rider_id: uuid.UUID) -> RiderProfile:
    rider = db.scalar(_listed_riders().where(RiderProfile.user_id == rider_id))
    if rider is None:
        raise not_found("Rider")
    return rider


def can_take_booking(
    db: Session, rider: RiderProfile, pickup_area_id: uuid.UUID, at: datetime | None
) -> bool:
    if pickup_area_id not in {area.id for area in rider.service_areas}:
        return False
    statement = (
        select(RiderProfile.user_id)
        .where(RiderProfile.user_id == rider.user_id)
        .where(*_available_filter(pickup_area_id, at))
    )
    return db.scalar(statement) is not None


def replace_slots(db: Session, rider: RiderProfile, slots: list) -> None:
    """Closes this rider's upcoming open slots and opens the supplied ones."""
    now = utcnow()
    served = {area.id for area in rider.service_areas}
    ordered = sorted(slots, key=lambda s: s.starts_at)

    for slot in ordered:
        if slot.ends_at <= now:
            raise AppError(422, "SLOT_IN_PAST", "Time slots must end in the future")
        if slot.service_area_id not in served:
            raise AppError(422, "AREA_NOT_SERVED", "Add this area to your profile first")
    for first, second in zip(ordered, ordered[1:], strict=False):
        if second.starts_at < first.ends_at:
            raise AppError(422, "SLOTS_OVERLAP", "Time slots must not overlap")

    upcoming = db.scalars(
        select(AvailabilitySlot).where(
            AvailabilitySlot.rider_id == rider.user_id,
            AvailabilitySlot.status == SlotStatus.OPEN,
            AvailabilitySlot.ends_at > now,
        )
    )
    for existing in upcoming:
        existing.status = SlotStatus.CLOSED
    db.add_all(
        AvailabilitySlot(
            rider_id=rider.user_id,
            service_area_id=slot.service_area_id,
            starts_at=slot.starts_at,
            ends_at=slot.ends_at,
        )
        for slot in ordered
    )


def upcoming_slots(db: Session, rider_id: uuid.UUID) -> list[AvailabilitySlot]:
    return list(
        db.scalars(
            select(AvailabilitySlot)
            .where(
                AvailabilitySlot.rider_id == rider_id,
                AvailabilitySlot.status == SlotStatus.OPEN,
                AvailabilitySlot.ends_at > utcnow(),
            )
            .order_by(AvailabilitySlot.starts_at)
        )
    )
