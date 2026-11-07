import uuid

from fastapi import APIRouter, Query
from pydantic import AwareDatetime
from sqlalchemy import select

from app import presenters, schemas
from app.db import utcnow
from app.deps import Db, Rider
from app.errors import AppError, not_found
from app.models import (
    Booking,
    BookingStatus,
    Rating,
    RatingStatus,
    RiderProfile,
    RiderServiceArea,
    ServiceArea,
    VerificationStatus,
)
from app.services import explanations, media, payments, riders

router = APIRouter(prefix="/api/riders", tags=["riders"])


def _my_profile(db, user) -> RiderProfile:
    profile = db.get(RiderProfile, user.id)
    if profile is None:
        raise AppError(404, "NO_PROFILE", "Create your rider profile first")
    return profile


def _my_rider_out(db, profile: RiderProfile) -> schemas.MyRiderOut:
    detail = presenters.rider_detail(db, profile)
    balance = payments.rider_balance_minor(db, profile.user_id)
    db.commit()  # the first balance lookup may create the rider's ledger account
    return schemas.MyRiderOut(**detail.model_dump(), balance_minor=balance)


@router.get("", response_model=list[schemas.RiderListItem], summary="List riders for choice")
def list_riders(
    db: Db,
    area: uuid.UUID | None = Query(default=None, description="Pickup service area id"),
    time: AwareDatetime | None = Query(default=None, description="Omit to mean now"),
    sort: schemas.Sort = "trust",
    include_demo: bool = True,
):
    found, stats, distances = riders.search(db, area, time, sort, include_demo)
    images = media.profile_urls(db, [r.user_id for r in found])
    return [
        presenters.rider_list_item(r, stats[r.user_id], distances[r.user_id], images.get(r.user_id))
        for r in found
    ]


# The /me routes must stay above /{rider_id}.
@router.get("/me", response_model=schemas.MyRiderOut)
def my_profile(user: Rider, db: Db):
    return _my_rider_out(db, _my_profile(db, user))


@router.put("/me", response_model=schemas.MyRiderOut)
def save_my_profile(body: schemas.RiderProfileIn, user: Rider, db: Db):
    area_ids = set(body.service_area_ids)
    found = db.scalars(select(ServiceArea.id).where(ServiceArea.id.in_(area_ids))).all()
    if len(found) != len(area_ids):
        raise not_found("Service area")

    profile = db.get(RiderProfile, user.id)
    if profile is None:
        profile = RiderProfile(
            user_id=user.id,
            display_name=body.display_name,
            vehicle_type=body.vehicle_type,
            verification_status=(
                VerificationStatus.PHONE_CONFIRMED
                if user.phone_confirmed_at
                else VerificationStatus.UNVERIFIED
            ),
        )
        db.add(profile)
        db.flush()
    profile.display_name = body.display_name
    profile.vehicle_type = body.vehicle_type

    existing = db.scalars(
        select(RiderServiceArea).where(RiderServiceArea.rider_id == user.id)
    ).all()
    for row in existing:
        if row.service_area_id not in area_ids:
            db.delete(row)
    current = {row.service_area_id for row in existing}
    db.add_all(
        RiderServiceArea(rider_id=user.id, service_area_id=area_id)
        for area_id in area_ids - current
    )
    db.commit()
    db.refresh(profile, ["service_areas"])
    return _my_rider_out(db, profile)


@router.put("/me/availability", response_model=schemas.MyRiderOut)
def update_availability(body: schemas.AvailabilityIn, user: Rider, db: Db):
    profile = _my_profile(db, user)
    if body.is_available is not None:
        profile.is_available = body.is_available
    if body.current_area_id is not None:
        if db.get(ServiceArea, body.current_area_id) is None:
            raise not_found("Service area")
        profile.current_area_id = body.current_area_id
    if body.slots is not None:
        riders.replace_slots(db, profile, body.slots)
    profile.availability_updated_at = utcnow()
    db.commit()
    db.refresh(profile, ["current_area"])
    return _my_rider_out(db, profile)


@router.get("/me/reviews", response_model=list[schemas.MyReview])
def my_reviews(user: Rider, db: Db):
    return db.scalars(
        select(Rating)
        .join(Booking, Booking.id == Rating.booking_id)
        .where(Booking.rider_id == user.id)
        .order_by(Rating.created_at.desc())
        .limit(50)
    ).all()


@router.get("/{rider_id}", response_model=schemas.RiderDetail)
def rider_profile(rider_id: uuid.UUID, db: Db):
    return presenters.rider_detail(db, riders.get_listed_rider(db, rider_id))


@router.get("/{rider_id}/reviews", response_model=list[schemas.PublicReview])
def rider_reviews(rider_id: uuid.UUID, db: Db):
    """Public reviews carry no author, and no free text until moderation exists."""
    riders.get_listed_rider(db, rider_id)
    return db.scalars(
        select(Rating)
        .join(Booking, Booking.id == Rating.booking_id)
        .where(
            Booking.rider_id == rider_id,
            Booking.status == BookingStatus.DELIVERED,
            Rating.status == RatingStatus.ACTIVE,
        )
        .order_by(Rating.created_at.desc())
        .limit(20)
    ).all()


@router.get("/{rider_id}/explanation", response_model=schemas.ExplanationOut)
def score_explanation(rider_id: uuid.UUID, db: Db):
    riders.get_listed_rider(db, rider_id)
    stats = riders.stats_for(db, [rider_id])[rider_id]
    text, source = explanations.explain(stats)
    return schemas.ExplanationOut(text=text, source=source)
