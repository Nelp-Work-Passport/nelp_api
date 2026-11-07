"""Clearly labelled demo data. Run with: python -m app.seed

Safe to run again: it adds what is missing and refreshes demo availability.
"""

import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal, create_tables, utcnow
from app.models import (
    AvailabilitySlot,
    Booking,
    BookingStatus,
    DeliveryConfirmation,
    Rating,
    RiderProfile,
    Role,
    ServiceArea,
    SlotStatus,
    User,
    UserRole,
    VerificationStatus,
)
from app.security import hash_pin
from app.services import fares

NAIROBI = ZoneInfo("Africa/Nairobi")

# Approximate neighbourhood centres. Used only for coarse distance and fares.
AREAS = {
    "CBD": (-1.2864, 36.8172),
    "Westlands": (-1.2676, 36.8108),
    "Kilimani": (-1.2921, 36.7856),
    "Kileleshwa": (-1.2792, 36.7836),
    "Parklands": (-1.2610, 36.8180),
    "South B": (-1.3106, 36.8350),
    "South C": (-1.3190, 36.8250),
    "Lang'ata": (-1.3535, 36.7500),
    "Karen": (-1.3195, 36.7073),
    "Eastleigh": (-1.2740, 36.8510),
    "Kasarani": (-1.2219, 36.8967),
    "Embakasi": (-1.3200, 36.9000),
}

# name, phone, vehicle, areas, current area, star counts {stars: how many}
RIDERS = [
    (
        "Amina W. (demo)",
        "+254700000001",
        "Motorbike",
        ["CBD", "Westlands", "Parklands"],
        "Westlands",
        {5: 14, 4: 6},
    ),
    (
        "Brian O. (demo)",
        "+254700000002",
        "Motorbike",
        ["CBD", "Kilimani", "Kileleshwa"],
        "Kilimani",
        {5: 6, 4: 5, 3: 1},
    ),
    (
        "Chebet K. (demo)",
        "+254700000003",
        "Bicycle",
        ["CBD", "Westlands", "Kileleshwa"],
        "CBD",
        {5: 2, 4: 3, 3: 3},
    ),
    (
        "David M. (demo)",
        "+254700000004",
        "Van",
        ["CBD", "South B", "South C", "Embakasi"],
        "South B",
        {5: 1},
    ),
    (
        "Esther N. (demo)",
        "+254700000005",
        "Motorbike",
        ["CBD", "Eastleigh", "Kasarani"],
        "Eastleigh",
        {5: 1, 4: 1},
    ),
    ("Felix A. (demo)", "+254700000006", "Motorbike", ["CBD", "Karen", "Lang'ata"], "Lang'ata", {}),
]
CUSTOMER_COUNT = 8


def _user(db: Session, phone: str, name: str, pin: str, roles: list[Role]) -> User:
    user = db.scalar(select(User).where(User.phone == phone))
    if user:
        return user
    user = User(
        phone=phone,
        pin_hash=hash_pin(pin),
        display_name=name,
        phone_confirmed_at=utcnow(),
        role_rows=[UserRole(role=role) for role in roles],
    )
    db.add(user)
    db.flush()
    return user


def _history(db, rider: User, areas, customers, star_counts) -> None:
    """Real delivered bookings and ratings, so the demo score uses the real rules."""
    stars_list = [stars for stars, count in star_counts.items() for _ in range(count)]
    quote = fares.quote(areas[0], areas[-1])
    for index, stars in enumerate(stars_list):
        when = utcnow() - timedelta(days=8 * index + 2)
        booking = Booking(
            requester_id=customers[index % len(customers)].id,
            recipient_phone=customers[index % len(customers)].phone,
            recipient_user_id=customers[index % len(customers)].id,
            rider_id=rider.id,
            pickup_area_id=areas[0].id,
            dropoff_area_id=areas[-1].id,
            scheduled_at=when,
            ends_at=when + timedelta(hours=1),
            fare_minor=quote.fare_minor,
            platform_fee_minor=quote.platform_fee_minor,
            status=BookingStatus.DELIVERED,
            created_at=when,
        )
        db.add(booking)
        db.flush()
        db.add(
            DeliveryConfirmation(
                booking_id=booking.id,
                code_hash="seeded",
                expires_at=when,
                sent_at=when,
                confirmed_at=when,
            )
        )
        db.add(
            Rating(
                booking_id=booking.id,
                author_id=booking.requester_id,
                stars=stars,
                tags=["punctual"] if stars >= 4 else [],
                created_at=when + timedelta(hours=2),
            )
        )


def _refresh_slots(db: Session, rider_id: uuid.UUID, area_id: uuid.UUID) -> None:
    open_slots = list(
        db.scalars(
            select(AvailabilitySlot).where(
                AvailabilitySlot.rider_id == rider_id, AvailabilitySlot.status == SlotStatus.OPEN
            )
        )
    )
    # Free-plan apps restart whenever they wake, so most starts have nothing to do.
    if any(slot.ends_at > utcnow() + timedelta(days=3) for slot in open_slots):
        return
    for slot in open_slots:
        slot.status = SlotStatus.CLOSED
    today = utcnow().astimezone(NAIROBI).date()
    for offset in range(7):
        day = today + timedelta(days=offset)
        db.add(
            AvailabilitySlot(
                rider_id=rider_id,
                service_area_id=area_id,
                starts_at=datetime.combine(day, time(7), NAIROBI),
                ends_at=datetime.combine(day, time(21), NAIROBI),
            )
        )


def seed(db: Session) -> None:
    settings = get_settings()
    areas = {area.name: area for area in db.scalars(select(ServiceArea))}
    for name, (latitude, longitude) in AREAS.items():
        if name not in areas:
            areas[name] = ServiceArea(
                name=name, city="Nairobi", latitude=latitude, longitude=longitude
            )
            db.add(areas[name])
    db.flush()

    customers = [
        _user(
            db,
            f"+2547000001{index:02d}",
            f"Demo Customer {index}",
            settings.demo_pin,
            [Role.CUSTOMER, Role.MERCHANT],
        )
        for index in range(1, CUSTOMER_COUNT + 1)
    ]

    for name, phone, vehicle, area_names, current, star_counts in RIDERS:
        user = _user(db, phone, name, settings.demo_pin, [Role.RIDER])
        rider_areas = [areas[n] for n in area_names]
        if db.get(RiderProfile, user.id) is None:
            db.add(
                RiderProfile(
                    user_id=user.id,
                    display_name=name,
                    vehicle_type=vehicle,
                    verification_status=VerificationStatus.PHONE_CONFIRMED,
                    is_available=True,
                    availability_updated_at=utcnow(),
                    current_area_id=areas[current].id,
                    is_demo=True,
                    service_areas=rider_areas,
                )
            )
            db.flush()
            _history(db, user, rider_areas, customers, star_counts)
        _refresh_slots(db, user.id, rider_areas[0].id)

    if settings.admin_phone and settings.admin_pin:
        _user(db, settings.admin_phone, "Nelp Admin", settings.admin_pin, [Role.ADMIN])
    db.commit()


def main() -> None:
    create_tables()
    with SessionLocal() as db:
        seed(db)
    print(f"Seeded {len(AREAS)} areas and {len(RIDERS)} demo riders.")


if __name__ == "__main__":
    main()
