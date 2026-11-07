"""Suspicious rating patterns, for a person to review.

A flag is a prompt to look, not proof of abuse. Nothing here changes a score or
hides a rider. Device reuse is not checked because the API collects no device data.
"""

from collections import Counter, defaultdict
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import Booking, Rating, RatingStatus, User

LOOKBACK_DAYS = 30
PAIR_THRESHOLD = 3
BURST_THRESHOLD = 5
BURST_WINDOW = timedelta(hours=1)


def suspicious_patterns(db: Session) -> list[dict]:
    since = utcnow() - timedelta(days=LOOKBACK_DAYS)
    rows = db.execute(
        select(Rating.author_id, Booking.rider_id, Rating.created_at)
        .join(Booking, Booking.id == Rating.booking_id)
        .where(Rating.created_at >= since, Rating.status == RatingStatus.ACTIVE)
    ).all()

    flags = []
    pairs = Counter((author, rider) for author, rider, _ in rows)
    for (author, rider), count in pairs.items():
        if count >= PAIR_THRESHOLD:
            flags.append(
                {
                    "type": "REPEATED_PAIR",
                    "rider_id": rider,
                    "author_id": author,
                    "detail": f"{count} ratings from one account in {LOOKBACK_DAYS} days",
                }
            )

    times_by_rider = defaultdict(list)
    for _, rider, created_at in rows:
        times_by_rider[rider].append(created_at)
    for rider, times in times_by_rider.items():
        times.sort()
        most = max(
            sum(1 for t in times[i:] if t - start <= BURST_WINDOW) for i, start in enumerate(times)
        )
        if most >= BURST_THRESHOLD:
            flags.append(
                {
                    "type": "RATING_BURST",
                    "rider_id": rider,
                    "author_id": None,
                    "detail": f"{most} ratings within one hour",
                }
            )

    linked = db.execute(
        select(Booking.rider_id, Booking.requester_id)
        .join(User, User.id == Booking.rider_id)
        .where(Booking.recipient_phone == User.phone, Booking.created_at >= since)
        .distinct()
    ).all()
    for rider, requester in linked:
        flags.append(
            {
                "type": "LINKED_PHONE",
                "rider_id": rider,
                "author_id": requester,
                "detail": "The recipient's phone is the rider's own phone",
            }
        )
    return flags
