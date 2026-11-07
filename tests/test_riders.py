from datetime import timedelta

from tests.conftest import tomorrow


def list_ids(client, **params):
    response = client.get("/api/riders", params=params)
    assert response.status_code == 200, response.text
    return [rider["id"] for rider in response.json()]


def test_rider_appears_with_trust_fields(world, client):
    rider = world.rider()
    item = client.get("/api/riders", params={"area": world.cbd}).json()[0]
    assert item["id"] == rider["id"]
    assert item["trust"]["label"] == "New · 0 ratings"
    assert item["trust"]["completed_deliveries"] == 0
    assert item["verification"] == {"status": "PHONE_CONFIRMED", "label": "Phone confirmed"}
    assert item["approx_distance_km"] == 0.0


def test_public_rider_data_has_no_phone(world, client):
    rider = world.rider()
    for path in (f"/api/riders/{rider['id']}", "/api/riders"):
        assert rider["phone"][1:] not in client.get(path).text


def test_unconfirmed_rider_is_not_listed(world, client):
    rider = world.register(("rider",), "Hidden", confirm=False)
    client.put(
        "/api/riders/me",
        json={"display_name": "Hidden", "vehicle_type": "Bike", "service_area_ids": [world.cbd]},
        headers=rider["headers"],
    )
    client.put("/api/riders/me/availability", json={"is_available": True}, headers=rider["headers"])
    assert list_ids(client) == []
    assert client.get(f"/api/riders/{rider['id']}").status_code == 404


def test_toggle_controls_now_and_slots_control_future(world, client):
    rider = world.rider(available=False)
    assert list_ids(client, area=world.cbd) == []
    start = tomorrow()
    assert list_ids(client, area=world.cbd, time=start.isoformat()) == []
    world.open_slot(rider, start)
    assert list_ids(client, area=world.cbd, time=(start + timedelta(hours=1)).isoformat()) == [
        rider["id"]
    ]
    # The last hour of the slot cannot fit a one-hour booking.
    late = start + timedelta(hours=7, minutes=30)
    assert list_ids(client, area=world.cbd, time=late.isoformat()) == []


def test_overlapping_slots_are_rejected(world, client):
    rider = world.rider()
    start = tomorrow()
    slots = [
        {
            "service_area_id": world.cbd,
            "starts_at": (start + timedelta(hours=h)).isoformat(),
            "ends_at": (start + timedelta(hours=h + 2)).isoformat(),
        }
        for h in (0, 1)
    ]
    response = client.put(
        "/api/riders/me/availability", json={"slots": slots}, headers=rider["headers"]
    )
    assert response.status_code == 422
    assert response.json()["code"] == "SLOTS_OVERLAP"


def test_slot_outside_served_area_is_rejected(world, client):
    rider = world.rider()
    client.put(
        "/api/riders/me",
        json={
            "display_name": "Rider",
            "vehicle_type": "Bike",
            "service_area_ids": [world.westlands],
        },
        headers=rider["headers"],
    )
    start = tomorrow()
    response = client.put(
        "/api/riders/me/availability",
        json={
            "slots": [
                {
                    "service_area_id": world.cbd,
                    "starts_at": start.isoformat(),
                    "ends_at": (start + timedelta(hours=1)).isoformat(),
                }
            ]
        },
        headers=rider["headers"],
    )
    assert response.json()["code"] == "AREA_NOT_SERVED"


def test_accepted_booking_reserves_the_time(world, client):
    rider = world.rider()
    customer = world.customer()
    start = tomorrow()
    world.open_slot(rider, start)
    booking_id = world.booking(customer, rider, scheduled_at=start.isoformat())
    assert list_ids(client, area=world.cbd, time=start.isoformat()) == [rider["id"]]
    world.act(booking_id, "accept", rider)
    assert list_ids(client, area=world.cbd, time=start.isoformat()) == []
    after = start + timedelta(hours=2)
    assert list_ids(client, area=world.cbd, time=after.isoformat()) == [rider["id"]]


def test_new_riders_stay_listed_below_established_ones(world, client):
    established = world.rider(name="Established")
    new = world.rider(name="Newcomer")
    customer = world.customer()
    for _ in range(3):
        booking_id = world.deliver(customer, established)
        world.act(booking_id, "ratings", customer, {"stars": 1})
    ids = list_ids(client, area=world.cbd, sort="trust")
    assert ids == [established["id"], new["id"]]


def test_sort_by_distance_puts_unknown_last(world, client):
    near = world.rider(name="Near")
    far = world.rider(name="Far")
    client.put(
        "/api/riders/me/availability",
        json={"current_area_id": world.westlands},
        headers=far["headers"],
    )
    unknown = world.register(("rider",), "Unknown")
    client.put(
        "/api/riders/me",
        json={"display_name": "Un", "vehicle_type": "Bike", "service_area_ids": [world.cbd]},
        headers=unknown["headers"],
    )
    client.put(
        "/api/riders/me/availability", json={"is_available": True}, headers=unknown["headers"]
    )
    assert list_ids(client, area=world.cbd, sort="distance") == [
        near["id"],
        far["id"],
        unknown["id"],
    ]


def test_customer_cannot_use_rider_routes(world, client):
    customer = world.customer()
    response = client.put(
        "/api/riders/me/availability", json={"is_available": True}, headers=customer["headers"]
    )
    assert response.status_code == 403


def test_fare_estimate_splits_the_fare(world, client):
    fare = client.get(
        "/api/fares/estimate",
        params={"pickup_area_id": world.cbd, "dropoff_area_id": world.westlands},
    ).json()
    assert fare["fare_minor"] == fare["platform_fee_minor"] + fare["rider_earning_minor"]
    assert fare["currency"] == "KES"
    assert fare["fare_minor"] % 1000 == 0
