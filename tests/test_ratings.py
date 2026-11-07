from datetime import timedelta

import pytest

from app.db import utcnow
from app.models import Rating
from app.services.flags import suspicious_patterns


def trust(client, rider):
    return client.get(f"/api/riders/{rider['id']}").json()["trust"]


@pytest.mark.parametrize("stage", ["REQUESTED", "ACCEPTED", "PICKED_UP", "CANCELLED", "DECLINED"])
def test_only_delivered_bookings_can_be_rated(world, client, stage):
    """DoD 3."""
    rider = world.rider()
    customer = world.customer()
    booking_id = world.booking(customer, rider)
    if stage in ("ACCEPTED", "PICKED_UP"):
        world.act(booking_id, "accept", rider)
    if stage == "PICKED_UP":
        world.act(booking_id, "status", rider, {"status": "PICKED_UP"})
    if stage == "CANCELLED":
        world.act(booking_id, "cancel", customer, {})
    if stage == "DECLINED":
        world.act(booking_id, "decline", rider, {})
    response = world.act(booking_id, "ratings", customer, {"stars": 5})
    assert response.status_code == 409
    assert response.json()["code"] == "NOT_RATEABLE"
    assert trust(client, rider)["rated_bookings"] == 0


def test_one_rating_per_author_per_booking(world):
    rider = world.rider()
    customer = world.customer()
    booking_id = world.deliver(customer, rider)
    assert world.act(booking_id, "ratings", customer, {"stars": 5}).status_code == 201
    response = world.act(booking_id, "ratings", customer, {"stars": 1})
    assert response.status_code == 409
    assert response.json()["code"] == "ALREADY_RATED"


def test_rider_and_strangers_cannot_rate(world):
    rider = world.rider()
    customer = world.customer()
    booking_id = world.deliver(customer, rider)
    assert world.act(booking_id, "ratings", rider, {"stars": 5}).status_code == 403
    assert world.act(booking_id, "ratings", world.customer(), {"stars": 5}).status_code == 403


@pytest.mark.parametrize("stars", [0, 6])
def test_stars_must_be_one_to_five(world, stars):
    customer = world.customer()
    booking_id = world.deliver(customer, world.rider())
    assert world.act(booking_id, "ratings", customer, {"stars": stars}).status_code == 422


def test_merchant_and_recipient_count_as_one_delivery(world, client):
    rider = world.rider()
    merchant = world.register(("merchant",), "Duka")
    recipient = world.customer()
    booking_id = world.booking(merchant, rider, recipient_phone=recipient["phone"])
    world.act(booking_id, "accept", rider)
    world.act(booking_id, "status", rider, {"status": "PICKED_UP"})
    code = world.latest_code(recipient["phone"])
    world.act(booking_id, "confirm-delivery", rider, {"code": code})
    assert world.act(booking_id, "ratings", merchant, {"stars": 5}).status_code == 201
    assert world.act(booking_id, "ratings", recipient, {"stars": 4}).status_code == 201
    assert trust(client, rider)["rated_bookings"] == 1


def test_score_appears_at_three_rated_bookings(world, client):
    rider = world.rider()
    customer = world.customer()
    for _ in range(3):
        assert trust(client, rider)["is_new"] is True
        booking_id = world.deliver(customer, rider)
        world.act(booking_id, "ratings", customer, {"stars": 5})
    summary = trust(client, rider)
    # (15 + 17.5) / 8 = 4.0625, times 20 = 81.25
    assert summary["trust_score"] == 81
    assert summary["label"] == "81 · 3 ratings"
    assert summary["average_stars"] == 5.0


def test_public_reviews_have_no_author_or_free_text(world, client):
    rider = world.rider()
    customer = world.customer()
    booking_id = world.deliver(customer, rider)
    world.act(booking_id, "ratings", customer, {"stars": 4, "comment": "Rude to my mother"})
    response = client.get(f"/api/riders/{rider['id']}/reviews")
    assert response.json()[0]["stars"] == 4
    assert "mother" not in response.text
    assert customer["id"] not in response.text
    mine = client.get("/api/riders/me/reviews", headers=rider["headers"]).json()
    assert mine[0]["comment"] == "Rude to my mother"


def test_report_upheld_removes_rating_from_score(world, client):
    rider = world.rider()
    customer = world.customer()
    admin = world.admin()
    booking_id = world.deliver(customer, rider)
    rating_id = world.act(booking_id, "ratings", customer, {"stars": 1}).json()["id"]

    report = client.post(
        f"/api/ratings/{rating_id}/report",
        json={"reason": "This delivery never had a problem"},
        headers=rider["headers"],
    )
    assert report.status_code == 201
    # An open report changes nothing until a person reviews it.
    assert trust(client, rider)["rated_bookings"] == 1

    queue = client.get("/api/admin/rating-reports", headers=admin["headers"]).json()
    assert len(queue) == 1
    resolved = client.post(
        f"/api/admin/rating-reports/{report.json()['id']}/resolve",
        json={"uphold": True},
        headers=admin["headers"],
    )
    assert resolved.json()["status"] == "UPHELD"
    assert trust(client, rider)["rated_bookings"] == 0


def test_strangers_cannot_report(world, client):
    customer = world.customer()
    booking_id = world.deliver(customer, world.rider())
    rating_id = world.act(booking_id, "ratings", customer, {"stars": 1}).json()["id"]
    response = client.post(
        f"/api/ratings/{rating_id}/report",
        json={"reason": "I do not like this"},
        headers=world.customer()["headers"],
    )
    assert response.status_code == 404


def test_flags_prompt_review_without_changing_the_score(world, client, db):
    rider = world.rider()
    customer = world.customer()
    admin = world.admin()
    for _ in range(3):
        booking_id = world.deliver(customer, rider)
        world.act(booking_id, "ratings", customer, {"stars": 5})
    before = trust(client, rider)
    response = client.get("/api/admin/flags", headers=admin["headers"]).json()
    assert [flag["type"] for flag in response] == ["REPEATED_PAIR"]
    assert response[0]["rider_id"] == rider["id"]
    assert trust(client, rider) == before
    assert client.get(f"/api/riders/{rider['id']}").status_code == 200


def test_burst_and_linked_phone_flags(world, db):
    rider = world.rider()
    for _ in range(5):
        customer = world.customer()
        booking_id = world.deliver(customer, rider)
        world.act(booking_id, "ratings", customer, {"stars": 5})
    linked = world.customer()
    world.booking(linked, rider, recipient_phone=rider["phone"])
    kinds = {flag["type"] for flag in suspicious_patterns(db)}
    assert kinds == {"RATING_BURST", "LINKED_PHONE"}


def test_old_ratings_leave_the_flag_window(world, db):
    rider = world.rider()
    customer = world.customer()
    for _ in range(3):
        booking_id = world.deliver(customer, rider)
        world.act(booking_id, "ratings", customer, {"stars": 5})
    for rating in db.query(Rating):
        rating.created_at = utcnow() - timedelta(days=40)
    db.commit()
    assert suspicious_patterns(db) == []


def test_admin_routes_refuse_others(world, client):
    customer = world.customer()
    for path in ("/api/admin/flags", "/api/admin/rating-reports", "/api/admin/audit-events"):
        assert client.get(path, headers=customer["headers"]).status_code == 403
        assert client.get(path).status_code == 401
