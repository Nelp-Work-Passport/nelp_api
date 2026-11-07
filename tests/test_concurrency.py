"""Races that only a real database can show. Skipped on SQLite."""

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.adapters.payments import ProviderEvent
from app.config import get_settings
from app.db import SessionLocal
from app.errors import AppError
from app.models import (
    Booking,
    DeliveryConfirmation,
    LedgerTransaction,
    Payment,
    PaymentStatus,
    Rating,
    User,
)
from app.services import bookings, payments, ratings, riders
from tests.conftest import tomorrow

pytestmark = pytest.mark.skipif(
    get_settings().database_url.startswith("sqlite"),
    reason="needs PostgreSQL row locks",
)


def race(*calls) -> list:
    """Starts every call at the same moment. Returns each result or exception."""
    barrier = threading.Barrier(len(calls))

    def run(call):
        with SessionLocal() as db:
            barrier.wait()
            try:
                return call(db)
            except Exception as exc:  # noqa: BLE001
                return exc

    with ThreadPoolExecutor(len(calls)) as pool:
        return list(pool.map(run, calls))


def wins(results) -> int:
    return sum(not isinstance(result, Exception) for result in results)


def test_overlapping_accepts_cannot_both_win(world, monkeypatch):
    # Hold the gap between the busy check and the commit open, so the race
    # happens every run instead of only when the timing is unlucky.
    real_check = riders.rider_is_busy

    def slow_check(*args):
        busy = real_check(*args)
        time.sleep(0.2)
        return busy

    monkeypatch.setattr(riders, "rider_is_busy", slow_check)

    rider = world.rider()
    start = tomorrow()
    world.open_slot(rider, start)
    ids = [world.booking(world.customer(), rider, scheduled_at=start.isoformat()) for _ in range(5)]

    def accept(booking_id):
        def call(db):
            return bookings.accept(db, booking_id, db.get(User, rider_uuid))

        return call

    rider_uuid = uuid.UUID(rider["id"])
    results = race(*[accept(uuid.UUID(booking_id)) for booking_id in ids])
    assert wins(results) == 1
    assert all(
        isinstance(r, AppError) and r.code == "SLOT_TAKEN"
        for r in results
        if isinstance(r, Exception)
    )
    with SessionLocal() as db:
        assert db.query(Booking).filter_by(status="ACCEPTED").count() == 1


def test_racing_intents_create_one_live_payment(world):
    customer = world.customer()
    booking_id = uuid.UUID(world.booking(customer, world.rider()))
    user_id = uuid.UUID(customer["id"])

    def intent(index):
        def call(db):
            return payments.create_intent(
                db, db.get(User, user_id), booking_id, f"race-key-{index:04d}"
            )

        return call

    results = race(*[intent(i) for i in range(6)])
    assert wins(results) == 1
    with SessionLocal() as db:
        assert db.query(Payment).count() == 1


def test_racing_callbacks_post_once(world):
    customer = world.customer()
    booking_id = world.booking(customer, world.rider())
    payment = world.pay(customer, booking_id, outcome=None)
    event = ProviderEvent(
        provider_reference=payment["provider_reference"],
        status=PaymentStatus.SUCCEEDED,
        amount_minor=payment["amount_minor"],
        currency="KES",
    )
    results = race(*[lambda db: payments.apply_provider_event(db, event) for _ in range(6)])
    assert wins(results) == 6
    with SessionLocal() as db:
        assert db.query(LedgerTransaction).count() == 1


def test_racing_ratings_keep_one(world):
    customer = world.customer()
    booking_id = uuid.UUID(world.deliver(customer, world.rider()))
    user_id = uuid.UUID(customer["id"])
    results = race(
        *[
            lambda db: ratings.rate_booking(db, booking_id, db.get(User, user_id), 5, [], None)
            for _ in range(5)
        ]
    )
    assert wins(results) == 1
    with SessionLocal() as db:
        assert db.query(Rating).count() == 1


def test_racing_wrong_codes_count_every_attempt(world):
    customer = world.customer()
    rider = world.rider()
    booking_id = uuid.UUID(world.to_picked_up(customer, rider))
    rider_id = uuid.UUID(rider["id"])
    real = world.latest_code(customer["phone"])
    wrong = "111111" if real != "111111" else "222222"
    race(
        *[
            lambda db: bookings.confirm_delivery(db, booking_id, db.get(User, rider_id), wrong)
            for _ in range(4)
        ]
    )
    with SessionLocal() as db:
        assert db.query(DeliveryConfirmation).one().attempts == 4
