from types import SimpleNamespace

import anthropic
import pytest

from app.adapters.sms import SmsSendError, ThriveVasSms
from app.config import get_settings
from app.db import SessionLocal
from app.models import SmsMessage
from app.seed import seed
from app.services import explanations, riders, scoring


@pytest.fixture
def sms_down(monkeypatch):
    def fail(self, phone, text):
        raise SmsSendError("provider down")

    monkeypatch.setattr("app.adapters.sms.DemoInboxSms.send", fail)


def test_sms_failure_is_visible_and_keeps_the_booking(world, client, monkeypatch):
    """DoD 6."""
    rider = world.rider()
    customer = world.customer()
    booking_id = world.booking(customer, rider)

    def fail(self, phone, text):
        raise SmsSendError("provider down")

    with monkeypatch.context() as patch:
        patch.setattr("app.adapters.sms.DemoInboxSms.send", fail)
        response = world.act(booking_id, "accept", rider)
    assert response.status_code == 200
    assert response.json()["status"] == "ACCEPTED"
    assert response.json()["delivery_code_sms"] == "FAILED"
    with SessionLocal() as db:
        assert db.query(SmsMessage).filter_by(status="FAILED").count() == 1

    # The safe resend path recovers the flow.
    resent = world.act(booking_id, "resend-code", customer).json()
    assert resent["delivery_code_sms"] == "SENT"
    world.act(booking_id, "status", rider, {"status": "PICKED_UP"})
    code = world.latest_code(customer["phone"])
    done = world.act(booking_id, "confirm-delivery", rider, {"code": code})
    assert done.json()["status"] == "DELIVERED"


def test_register_survives_sms_failure(client, sms_down):
    response = client.post(
        "/api/auth/register",
        json={"phone": "0712345678", "pin": "4821", "display_name": "Amina", "roles": ["rider"]},
    )
    assert response.status_code == 201
    assert response.json()["sms_status"] == "FAILED"


def test_resends_are_limited(world):
    rider = world.rider()
    customer = world.customer()
    booking_id = world.booking(customer, rider)
    world.act(booking_id, "accept", rider)
    codes = [world.act(booking_id, "resend-code", customer).status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429


def test_sms_text_matches_the_proposal(world):
    rider = world.rider(name="Amina")
    customer = world.customer()
    world.act(world.booking(customer, rider), "accept", rider)
    text = world.inbox(customer["phone"])[0]
    assert text.startswith("Nelp: A delivery with Amina is booked for ")
    assert "Confirm details: http" in text
    assert "Code: " in text


def test_thrivevas_adapter_fails_loudly_rather_than_guessing():
    with pytest.raises(SmsSendError):
        ThriveVasSms().send("+254712345678", "hello")


def test_demo_phones_never_reach_a_live_provider(db, monkeypatch):
    from app.adapters import sms

    monkeypatch.setattr(get_settings(), "sms_provider", "thrivevas")
    assert sms.gateway_for(db, "+254700000001").name == "demo_inbox"
    assert sms.gateway_for(db, "+254712345678").name == "thrivevas"


def test_demo_inbox_disappears_with_a_live_provider(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "sms_provider", "thrivevas")
    response = client.get("/api/demo/inbox", params={"phone": "0712345678"})
    assert response.status_code == 404


def test_dlr_needs_the_secret_and_updates_status(world, client):
    world.customer()
    with SessionLocal() as db:
        reference = db.query(SmsMessage).first().provider_reference
    body = {"provider_reference": reference, "status": "DELIVERED"}
    assert client.post("/api/sms/dlr", json=body).status_code == 401
    secret = get_settings().sms_dlr_secret
    response = client.post("/api/sms/dlr", json=body, headers={"X-Dlr-Secret": secret})
    assert response.json() == {"status": "DELIVERED"}


def _stats(rating_count=20, average="4.7"):
    from fractions import Fraction

    return riders.RiderStats(
        trust=scoring.compute_trust([Fraction(average)] * rating_count),
        completed_deliveries=rating_count,
        ratings_last_30_days=3,
    )


def _fake_client(text, stop_reason="end_turn"):
    response = SimpleNamespace(
        stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)]
    )
    create = lambda **kwargs: response  # noqa: E731
    return lambda **kwargs: SimpleNamespace(
        beta=SimpleNamespace(messages=SimpleNamespace(create=create))
    )


def test_template_used_without_a_key():
    text, source = explanations.explain(_stats())
    assert source == "template"
    assert "89 out of 100" in text
    assert "does not guarantee" in text


def test_new_rider_template_shows_no_score():
    text, source = explanations.explain(_stats(rating_count=1, average="5"))
    assert source == "template"
    assert "new" in text


def test_claude_text_is_used_when_figures_check_out(monkeypatch):
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    monkeypatch.setattr(
        anthropic, "Anthropic", _fake_client("This rider has 89 out of 100 from 20 ratings.")
    )
    text, source = explanations.explain(_stats())
    assert source == "claude"
    assert "89" in text


@pytest.mark.parametrize(
    ("reply", "stop"),
    [
        ("This rider scores 97 out of 100.", "end_turn"),
        ("", "end_turn"),
        ("Cut off", "max_tokens"),
        ("I cannot help.", "refusal"),
    ],
)
def test_bad_claude_output_falls_back(monkeypatch, reply, stop):
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    monkeypatch.setattr(anthropic, "Anthropic", _fake_client(reply, stop))
    text, source = explanations.explain(_stats())
    assert source == "template"
    assert "89 out of 100" in text


def test_seed_gives_the_proposals_example_rider(client, db):
    seed(db)
    seed(db)  # safe to run twice
    listed = client.get("/api/riders").json()
    assert len(listed) == 6
    assert all(r["is_demo"] and "(demo)" in r["display_name"] for r in listed)
    top = listed[0]
    assert top["display_name"].startswith("Amina")
    assert top["trust"]["trust_score"] == 89
    assert top["trust"]["rated_bookings"] == 20
    labels = {r["trust"]["label"] for r in listed}
    assert {"New · 0 ratings", "New · 1 rating", "New · 2 ratings"} <= labels
    assert client.get("/api/riders", params={"include_demo": False}).json() == []


def test_seeded_rider_is_bookable_at_a_future_time(client, db):
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from app.db import utcnow

    seed(db)
    area = next(a for a in client.get("/api/areas").json() if a["name"] == "CBD")
    day = (utcnow() + timedelta(days=1)).astimezone(ZoneInfo("Africa/Nairobi"))
    at = day.replace(hour=10, minute=0, second=0, microsecond=0)
    found = client.get("/api/riders", params={"area": area["id"], "time": at.isoformat()}).json()
    assert len(found) == 6


def test_health_and_docs(client):
    health = client.get("/health").json()
    assert health["status"] == "ok"
    assert health["payments_simulated"] is True
    paths = client.get("/openapi.json").json()["paths"]
    for path in (
        "/api/auth/register",
        "/api/auth/login",
        "/api/riders",
        "/api/riders/{rider_id}",
        "/api/riders/me/availability",
        "/api/bookings",
        "/api/bookings/{booking_id}",
        "/api/bookings/{booking_id}/accept",
        "/api/bookings/{booking_id}/status",
        "/api/bookings/{booking_id}/cancel",
        "/api/bookings/{booking_id}/confirm-delivery",
        "/api/bookings/{booking_id}/ratings",
        "/api/payments/intent",
        "/api/payments/webhook",
        "/api/sms/dlr",
        "/health",
    ):
        assert path in paths, path


def test_production_refuses_dev_secrets():
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError):
        Settings(environment="production")
