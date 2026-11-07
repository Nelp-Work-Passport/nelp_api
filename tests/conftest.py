"""Tests run on SQLite by default. Set TEST_DATABASE_URL to run them on PostgreSQL."""

import os
import re
import tempfile
from datetime import UTC, datetime, timedelta

_tmp = tempfile.mkdtemp(prefix="nelp-tests-")
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_tmp}/test.db")
os.environ["ENVIRONMENT"] = "test"
os.environ["SMS_PROVIDER"] = "demo"
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["SEED_ON_STARTUP"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.adapters import storage  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Role, ServiceArea, User, UserRole  # noqa: E402
from app.rate_limit import limiter  # noqa: E402
from app.security import hash_pin  # noqa: E402
from app.services.explanations import _ask_claude  # noqa: E402

# A developer's .env must never make the tests call a real paid service.
get_settings().anthropic_api_key = None


class MemoryStorage:
    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put(self, key, data, content_type):
        self.objects[key] = (data, content_type)

    def delete(self, key):
        self.objects.pop(key, None)

    def presigned_url(self, key, ttl_seconds):
        return f"https://storage.test/{key}?expires={ttl_seconds}"


@pytest.fixture(autouse=True)
def object_store(monkeypatch):
    memory = MemoryStorage()
    monkeypatch.setattr(storage, "get_storage", lambda: memory)
    monkeypatch.setattr(storage, "is_configured", lambda: True)
    return memory


@pytest.fixture(autouse=True)
def fresh_state():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    limiter.clear()
    _ask_claude.cache_clear()
    yield


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class World:
    """Drives the API the way the front end does."""

    def __init__(self, client: TestClient):
        self.client = client
        self.phone_counter = 0
        with SessionLocal() as session:
            near = ServiceArea(name="CBD", city="Nairobi", latitude=-1.2864, longitude=36.8172)
            far = ServiceArea(name="Westlands", city="Nairobi", latitude=-1.2676, longitude=36.8108)
            session.add_all([near, far])
            session.commit()
            self.cbd, self.westlands = str(near.id), str(far.id)

    def next_phone(self) -> str:
        self.phone_counter += 1
        return f"+25471{self.phone_counter:07d}"

    def inbox(self, phone: str) -> list[str]:
        response = self.client.get("/api/demo/inbox", params={"phone": phone})
        assert response.status_code == 200, response.text
        return [message["body"] for message in response.json()]

    def latest_code(self, phone: str) -> str:
        return re.findall(r"\b\d{6}\b", self.inbox(phone)[0])[-1]

    def register(self, roles=("customer",), name="Test User", confirm=True) -> dict:
        phone = self.next_phone()
        response = self.client.post(
            "/api/auth/register",
            json={"phone": phone, "pin": "4821", "display_name": name, "roles": list(roles)},
        )
        assert response.status_code == 201, response.text
        token = response.json()["access_token"]
        if confirm:
            confirmed = self.client.post(
                "/api/auth/confirm-phone",
                json={"code": self.latest_code(phone)},
                headers=auth(token),
            )
            assert confirmed.status_code == 200, confirmed.text
        return {
            "phone": phone,
            "token": token,
            "id": response.json()["user"]["id"],
            "headers": auth(token),
        }

    def customer(self) -> dict:
        return self.register(("customer",), "Wanjiku Customer")

    def admin(self) -> dict:
        with SessionLocal() as session:
            phone = self.next_phone()
            user = User(
                phone=phone,
                pin_hash=hash_pin("4821"),
                display_name="Admin",
                role_rows=[UserRole(role=Role.ADMIN)],
            )
            session.add(user)
            session.commit()
        response = self.client.post("/api/auth/login", json={"phone": phone, "pin": "4821"})
        return {"phone": phone, "headers": auth(response.json()["access_token"])}

    def rider(self, available=True, name="Amina Rider") -> dict:
        rider = self.register(("rider",), name)
        response = self.client.put(
            "/api/riders/me",
            json={
                "display_name": name,
                "vehicle_type": "Motorbike",
                "service_area_ids": [self.cbd, self.westlands],
            },
            headers=rider["headers"],
        )
        assert response.status_code == 200, response.text
        response = self.client.put(
            "/api/riders/me/availability",
            json={"is_available": available, "current_area_id": self.cbd},
            headers=rider["headers"],
        )
        assert response.status_code == 200, response.text
        return rider

    def open_slot(self, rider, start: datetime, hours=8):
        response = self.client.put(
            "/api/riders/me/availability",
            json={
                "slots": [
                    {
                        "service_area_id": self.cbd,
                        "starts_at": start.isoformat(),
                        "ends_at": (start + timedelta(hours=hours)).isoformat(),
                    }
                ]
            },
            headers=rider["headers"],
        )
        assert response.status_code == 200, response.text

    def book(self, customer, rider, **extra):
        body = {
            "rider_id": rider["id"],
            "pickup_area_id": self.cbd,
            "dropoff_area_id": self.westlands,
            **extra,
        }
        return self.client.post("/api/bookings", json=body, headers=customer["headers"])

    def booking(self, customer, rider, **extra) -> str:
        response = self.book(customer, rider, **extra)
        assert response.status_code == 201, response.text
        return response.json()["id"]

    def act(self, booking_id, action, who, json=None):
        return self.client.post(
            f"/api/bookings/{booking_id}/{action}", json=json, headers=who["headers"]
        )

    def to_picked_up(self, customer, rider) -> str:
        booking_id = self.booking(customer, rider)
        assert self.act(booking_id, "accept", rider).status_code == 200
        response = self.act(booking_id, "status", rider, {"status": "PICKED_UP"})
        assert response.status_code == 200, response.text
        return booking_id

    def deliver(self, customer, rider) -> str:
        booking_id = self.to_picked_up(customer, rider)
        code = self.latest_code(customer["phone"])
        response = self.act(booking_id, "confirm-delivery", rider, {"code": code})
        assert response.status_code == 200, response.text
        return booking_id

    def pay(self, customer, booking_id, key=None, outcome="SUCCEEDED") -> dict:
        response = self.client.post(
            "/api/payments/intent",
            json={"booking_id": booking_id},
            headers={**customer["headers"], "Idempotency-Key": key or f"key-{booking_id}"},
        )
        assert response.status_code == 200, response.text
        payment = response.json()
        if outcome:
            response = self.client.post(
                f"/api/payments/{payment['id']}/simulate",
                json={"outcome": outcome},
                headers=customer["headers"],
            )
            assert response.status_code == 200, response.text
        return response.json()

    def ledger(self, booking_id, who) -> dict:
        response = self.client.get(f"/api/bookings/{booking_id}/ledger", headers=who["headers"])
        assert response.status_code == 200, response.text
        return response.json()


@pytest.fixture
def world(client):
    return World(client)


def tomorrow(hour=9) -> datetime:
    day = datetime.now(UTC) + timedelta(days=1)
    return day.replace(hour=hour, minute=0, second=0, microsecond=0)
