from datetime import timedelta

from sqlalchemy import select

from app.db import utcnow
from app.models import PhoneConfirmation, User
from app.security import mask_phone, normalize_phone
from tests.conftest import auth


def test_phone_formats_normalise():
    for raw in ("0712345678", "+254712345678", "254712345678", "0712 345 678"):
        assert normalize_phone(raw) == "+254712345678"
    assert normalize_phone("0112345678") == "+254112345678"
    assert mask_phone("+254712345678") == "+2547*****678"


def test_register_login_and_me(world, client):
    user = world.register()
    response = client.post("/api/auth/login", json={"phone": user["phone"], "pin": "4821"})
    assert response.status_code == 200
    me = client.get("/api/auth/me", headers=auth(response.json()["access_token"])).json()
    assert me["phone_confirmed"] is True
    assert me["roles"] == ["customer"]


def test_nobody_can_register_as_admin(client):
    response = client.post(
        "/api/auth/register",
        json={"phone": "0712345678", "pin": "4821", "display_name": "Eve", "roles": ["admin"]},
    )
    assert response.status_code == 422


def test_pin_is_hashed_and_duplicate_phone_rejected(world, client, db):
    user = world.register()
    stored = db.scalar(select(User).where(User.phone == user["phone"]))
    assert "4821" not in stored.pin_hash
    response = client.post(
        "/api/auth/register",
        json={"phone": user["phone"], "pin": "4821", "display_name": "Twin", "roles": ["rider"]},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "PHONE_TAKEN"


def test_login_is_rate_limited(world, client):
    user = world.register()
    bad = {"phone": user["phone"], "pin": "0000"}
    codes = [client.post("/api/auth/login", json=bad).status_code for _ in range(9)]
    assert codes[:8] == [401] * 8
    assert codes[8] == 429


def test_bad_token_is_rejected(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers=auth("not-a-token")).status_code == 401


def test_phone_code_locks_after_wrong_attempts(world, client):
    user = world.register(confirm=False)
    real = world.latest_code(user["phone"])
    wrong = "111111" if real != "111111" else "222222"
    for _ in range(5):
        response = client.post(
            "/api/auth/confirm-phone", json={"code": wrong}, headers=user["headers"]
        )
        assert response.status_code == 400
    response = client.post("/api/auth/confirm-phone", json={"code": real}, headers=user["headers"])
    assert response.status_code == 429
    assert response.json()["code"] == "CODE_LOCKED"


def test_expired_phone_code_is_rejected(world, client, db):
    user = world.register(confirm=False)
    row = db.scalar(select(PhoneConfirmation))
    row.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    response = client.post(
        "/api/auth/confirm-phone",
        json={"code": world.latest_code(user["phone"])},
        headers=user["headers"],
    )
    assert response.status_code == 410


def test_unconfirmed_customer_cannot_book(world):
    customer = world.register(confirm=False)
    rider = world.rider()
    response = world.book(customer, rider)
    assert response.status_code == 403


def test_resend_replaces_the_code(world, client):
    user = world.register(confirm=False)
    old = world.latest_code(user["phone"])
    assert client.post("/api/auth/resend-code", headers=user["headers"]).status_code == 200
    new = world.latest_code(user["phone"])
    if old != new:
        response = client.post(
            "/api/auth/confirm-phone", json={"code": old}, headers=user["headers"]
        )
        assert response.status_code == 400
    response = client.post("/api/auth/confirm-phone", json={"code": new}, headers=user["headers"])
    assert response.status_code == 200
