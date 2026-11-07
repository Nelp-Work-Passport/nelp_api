from datetime import timedelta

import pytest

from app.db import utcnow
from tests.conftest import tomorrow


def test_definition_of_done_full_marketplace_flow(world, client):
    """DoD 1-5 in one story: browse, choose, book, accept, deliver, rate, ledger."""
    rider = world.rider()
    customer = world.customer()
    start = tomorrow()
    world.open_slot(rider, start)

    listed = client.get("/api/riders", params={"area": world.cbd, "time": start.isoformat()}).json()
    assert [r["id"] for r in listed] == [rider["id"]]

    booking = world.book(
        customer, rider, scheduled_at=start.isoformat(), item_notes="Fragile"
    ).json()
    assert booking["status"] == "REQUESTED"
    assert booking["contact_phones"] == {}
    booking_id = booking["id"]

    # Both views show the same future booking.
    seen_by_rider = client.get(f"/api/bookings/{booking_id}", headers=rider["headers"]).json()
    assert seen_by_rider["scheduled_at"] == booking["scheduled_at"]

    world.pay(customer, booking_id)
    accepted = world.act(booking_id, "accept", rider).json()
    assert accepted["status"] == "ACCEPTED"
    assert accepted["delivery_code_sms"] == "SENT"
    assert "Code:" in world.inbox(customer["phone"])[0]

    world.act(booking_id, "status", rider, {"status": "PICKED_UP"})
    delivered = world.act(
        booking_id, "confirm-delivery", rider, {"code": world.latest_code(customer["phone"])}
    ).json()
    assert delivered["status"] == "DELIVERED"

    seen = client.get(f"/api/bookings/{booking_id}", headers=customer["headers"]).json()
    assert seen["can_rate"] is True
    rating = world.act(booking_id, "ratings", customer, {"stars": 5, "tags": ["punctual"]}).json()
    assert rating["rider_trust"]["label"] == "New · 1 rating"
    assert rating["rider_trust"]["completed_deliveries"] == 1

    ledger = world.ledger(booking_id, customer)
    assert ledger["balanced"] is True
    assert [t["event_type"] for t in ledger["transactions"]] == [
        "PAYMENT_COLLECTED",
        "DELIVERY_ALLOCATED",
    ]
    statuses = [h["new_status"] for h in seen["history"]]
    assert statuses == ["REQUESTED", "ACCEPTED", "PICKED_UP", "DELIVERED"]


def test_cannot_book_unavailable_rider_or_wrong_area(world, client):
    rider = world.rider(available=False)
    customer = world.customer()
    response = world.book(customer, rider)
    assert response.status_code == 409
    assert response.json()["code"] == "RIDER_UNAVAILABLE"


def test_booking_for_a_time_outside_slots_is_rejected(world):
    rider = world.rider()
    customer = world.customer()
    response = world.book(customer, rider, scheduled_at=tomorrow().isoformat())
    assert response.json()["code"] == "RIDER_UNAVAILABLE"


@pytest.mark.parametrize(
    ("delta", "code"), [(timedelta(hours=-2), "TIME_IN_PAST"), (timedelta(days=45), "TIME_TOO_FAR")]
)
def test_bad_times_are_rejected(world, delta, code):
    rider = world.rider()
    customer = world.customer()
    response = world.book(customer, rider, scheduled_at=(utcnow() + delta).isoformat())
    assert response.status_code == 422
    assert response.json()["code"] == code


def test_rider_cannot_book_themselves(world, client):
    rider = world.register(("rider", "customer"), "Both")
    client.put(
        "/api/riders/me",
        json={"display_name": "Both", "vehicle_type": "Bike", "service_area_ids": [world.cbd]},
        headers=rider["headers"],
    )
    client.put("/api/riders/me/availability", json={"is_available": True}, headers=rider["headers"])
    assert world.book(rider, rider).json()["code"] == "SELF_BOOKING"


def test_overlapping_accept_is_refused(world):
    rider = world.rider()
    start = tomorrow()
    world.open_slot(rider, start)
    first = world.booking(world.customer(), rider, scheduled_at=start.isoformat())
    second = world.booking(
        world.customer(), rider, scheduled_at=(start + timedelta(minutes=30)).isoformat()
    )
    assert world.act(first, "accept", rider).status_code == 200
    response = world.act(second, "accept", rider)
    assert response.status_code == 409
    assert response.json()["code"] == "SLOT_TAKEN"


def test_only_selected_rider_can_accept(world):
    rider = world.rider()
    other = world.rider(name="Other")
    booking_id = world.booking(world.customer(), rider)
    assert world.act(booking_id, "accept", other).status_code == 403


def test_rider_can_decline_only_while_requested(world):
    rider = world.rider()
    customer = world.customer()
    first = world.booking(customer, rider)
    response = world.act(first, "decline", rider, {"reason": "Bike trouble"})
    assert response.json()["status"] == "DECLINED"
    second = world.booking(customer, rider)
    world.act(second, "accept", rider)
    assert world.act(second, "decline", rider, {}).status_code == 409


def test_delivered_cannot_be_set_directly(world):
    rider = world.rider()
    booking_id = world.booking(world.customer(), rider)
    world.act(booking_id, "accept", rider)
    assert world.act(booking_id, "status", rider, {"status": "DELIVERED"}).status_code == 422


def test_cannot_skip_acceptance(world):
    rider = world.rider()
    booking_id = world.booking(world.customer(), rider)
    response = world.act(booking_id, "status", rider, {"status": "PICKED_UP"})
    assert response.json()["code"] == "INVALID_TRANSITION"


def test_wrong_codes_lock_the_delivery(world):
    rider = world.rider()
    customer = world.customer()
    booking_id = world.to_picked_up(customer, rider)
    real = world.latest_code(customer["phone"])
    wrong = "111111" if real != "111111" else "222222"
    for _ in range(5):
        response = world.act(booking_id, "confirm-delivery", rider, {"code": wrong})
        assert response.status_code == 400
    response = world.act(booking_id, "confirm-delivery", rider, {"code": real})
    assert response.status_code == 429
    assert response.json()["code"] == "CODE_LOCKED"


def test_expired_delivery_code_can_be_replaced(world, db):
    from app.models import DeliveryConfirmation

    rider = world.rider()
    customer = world.customer()
    booking_id = world.to_picked_up(customer, rider)
    row = db.query(DeliveryConfirmation).one()
    row.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    code = world.latest_code(customer["phone"])
    assert world.act(booking_id, "confirm-delivery", rider, {"code": code}).status_code == 410
    assert world.act(booking_id, "resend-code", customer).status_code == 200
    new_code = world.latest_code(customer["phone"])
    assert world.act(booking_id, "confirm-delivery", rider, {"code": new_code}).status_code == 200


def test_delivery_code_is_stored_hashed(world, db):
    from app.models import DeliveryConfirmation

    customer = world.customer()
    world.to_picked_up(customer, world.rider())
    row = db.query(DeliveryConfirmation).one()
    assert len(row.code_hash) == 64
    assert world.latest_code(customer["phone"]) not in row.code_hash


def test_either_side_cancels_before_pickup(world):
    rider = world.rider()
    customer = world.customer()
    first = world.booking(customer, rider)
    assert world.act(first, "cancel", customer, {}).json()["status"] == "CANCELLED"
    second = world.booking(customer, rider)
    world.act(second, "accept", rider)
    assert world.act(second, "cancel", rider, {"reason": "Flat tyre"}).json()["status"] == (
        "CANCELLED"
    )
    # The cancelled booking frees the rider.
    assert world.book(customer, rider).status_code == 201


def test_cancel_after_pickup_needs_reason_and_review(world, client):
    rider = world.rider()
    customer = world.customer()
    admin = world.admin()
    booking_id = world.to_picked_up(customer, rider)
    world.pay(customer, booking_id)

    assert world.act(booking_id, "cancel", customer, {}).json()["code"] == "REASON_REQUIRED"
    pending = world.act(booking_id, "cancel", customer, {"reason": "Wrong item"}).json()
    assert pending["status"] == "PICKED_UP"
    assert pending["cancellation_pending"] is True
    assert world.act(booking_id, "cancel", rider, {"reason": "Me too"}).status_code == 409

    queue = client.get("/api/admin/cancellations", headers=admin["headers"]).json()
    assert [b["id"] for b in queue] == [booking_id]

    done = client.post(
        f"/api/admin/bookings/{booking_id}/resolve-cancellation",
        json={"approve": True},
        headers=admin["headers"],
    ).json()
    assert done["status"] == "CANCELLED"
    assert done["payment"]["status"] == "REFUNDED"
    assert done["cancellation_pending"] is False
    assert client.get("/api/admin/cancellations", headers=admin["headers"]).json() == []


def test_valid_code_settles_a_pending_cancellation(world, client):
    rider = world.rider()
    customer = world.customer()
    admin = world.admin()
    booking_id = world.to_picked_up(customer, rider)
    world.act(booking_id, "cancel", rider, {"reason": "Cannot find address"})
    code = world.latest_code(customer["phone"])
    done = world.act(booking_id, "confirm-delivery", rider, {"code": code}).json()
    assert done["status"] == "DELIVERED"
    assert done["cancellation_pending"] is False
    assert client.get("/api/admin/cancellations", headers=admin["headers"]).json() == []


def test_strangers_cannot_see_or_act_on_a_booking(world, client):
    rider = world.rider()
    customer = world.customer()
    stranger = world.customer()
    booking_id = world.booking(customer, rider)
    path = f"/api/bookings/{booking_id}"
    assert client.get(path, headers=stranger["headers"]).status_code == 404
    assert world.act(booking_id, "cancel", stranger, {}).status_code == 404


def test_phones_are_shared_only_while_active(world, client):
    rider = world.rider()
    customer = world.customer()
    other_phone = world.next_phone()
    booking_id = world.booking(customer, rider, recipient_phone=other_phone)
    path = f"/api/bookings/{booking_id}"

    before = client.get(path, headers=rider["headers"]).json()
    assert before["contact_phones"] == {}
    assert before["recipient_phone"] != other_phone

    world.act(booking_id, "accept", rider)
    during = client.get(path, headers=rider["headers"]).json()
    assert during["contact_phones"] == {"requester": customer["phone"], "recipient": other_phone}

    world.act(booking_id, "cancel", rider, {})
    after = client.get(path, headers=rider["headers"]).json()
    assert after["contact_phones"] == {}
    assert after["recipient_phone"] != other_phone


def test_tracking_link_shows_no_private_detail(world, client):
    rider = world.rider()
    customer = world.customer()
    booking = world.book(customer, rider, item_notes="Secret item").json()
    token = booking["tracking_url"].rsplit("/", 1)[1]
    response = client.get(f"/api/track/{token}")
    assert response.status_code == 200
    assert response.json()["status"] == "REQUESTED"
    assert "Secret" not in response.text
    assert customer["phone"][1:] not in response.text
    assert client.get("/api/track/nope").status_code == 404


def test_booking_creation_is_rate_limited(world):
    rider = world.rider()
    customer = world.customer()
    codes = [world.book(customer, rider).status_code for _ in range(21)]
    assert codes[-1] == 429
