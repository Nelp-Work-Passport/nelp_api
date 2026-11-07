"""/docs must tell the truth: every example and error is checked against the code."""

import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from pydantic import TypeAdapter

from app import api_docs
from app.routers import admin, auth, bookings, media, meta, misc, riders
from app.seed import seed

ROUTES = {
    f"{method} {route.path}": route
    for module in (admin, auth, bookings, media, meta, misc, riders)
    for route in module.router.routes
    if isinstance(route, APIRoute) and route.include_in_schema
    for method in route.methods
}
WITH_REQUEST = [k for k, op in api_docs.OPERATIONS.items() if op.request or op.variants]
WITH_RESPONSE = [
    k
    for k, op in api_docs.OPERATIONS.items()
    if op.response is not None and ROUTES[k].response_model is not None
]


def spec(client):
    return client.get("/openapi.json").json()


def body_model(route: APIRoute):
    fields = route.dependant.body_params
    assert len(fields) == 1
    return fields[0].field_info.annotation


def test_every_route_is_documented_and_nothing_is_stale():
    assert set(ROUTES) == set(api_docs.OPERATIONS)


def test_every_operation_is_in_the_spec(client):
    documented = {
        f"{method.upper()} {path}"
        for path, methods in spec(client)["paths"].items()
        for method in methods
    }
    assert documented == set(api_docs.OPERATIONS)


@pytest.mark.parametrize("key", WITH_REQUEST)
def test_request_examples_are_accepted_by_the_real_model(key):
    op = api_docs.OPERATIONS[key]
    if key == "POST /api/payments/webhook":  # read as raw bytes, checked by the gateway
        return
    adapter = TypeAdapter(body_model(ROUTES[key]))
    for example in [op.request] if op.request else op.variants.values():
        adapter.validate_python(example)


@pytest.mark.parametrize("key", WITH_RESPONSE)
def test_response_examples_match_the_real_model(key):
    TypeAdapter(ROUTES[key].response_model).validate_python(api_docs.OPERATIONS[key].response)


def test_every_json_body_has_an_example(client):
    missing = []
    for path, methods in spec(client)["paths"].items():
        for method, operation in methods.items():
            media_type = operation.get("requestBody", {}).get("content", {}).get("application/json")
            if media_type and not ("example" in media_type or "examples" in media_type):
                missing.append(f"{method} {path}")
    assert missing == []


def test_every_json_success_response_has_an_example(client):
    missing = []
    for path, methods in spec(client)["paths"].items():
        for method, operation in methods.items():
            for status, response in operation["responses"].items():
                media_type = response.get("content", {}).get("application/json")
                if status.startswith("2") and media_type and "example" not in media_type:
                    missing.append(f"{method} {path}")
    assert missing == []


def raised_in_source() -> dict[str, int]:
    """Every error code the application can raise, with its status."""
    text = "\n".join(p.read_text() for p in Path("app").rglob("*.py") if p.name != "api_docs.py")
    found = {
        code: int(status)
        for status, code in re.findall(r'AppError\(\s*(\d{3}),\s*"([A-Z_]+)"', text)
    }
    found.update(dict.fromkeys(re.findall(r'conflict\(\s*"([A-Z_]+)"', text), 409))
    return found


def test_error_catalogue_matches_the_source_exactly():
    assert {code: e[0] for code, e in api_docs.ERRORS.items()} == raised_in_source()


def test_every_catalogued_error_is_attached_to_a_route():
    used = {code for op in api_docs.OPERATIONS.values() for code in op.errors}
    assert used == set(api_docs.ERRORS)


def test_auth_errors_are_listed_on_every_protected_route():
    def needs(route: APIRoute, name: str) -> bool:
        def walk(dependant):
            return any(
                getattr(d.call, "__name__", "") == name or walk(d) for d in dependant.dependencies
            )

        return walk(route.dependant)

    for key, route in ROUTES.items():
        errors = api_docs.OPERATIONS[key].errors
        if needs(route, "current_user"):
            assert "UNAUTHENTICATED" in errors, key
        if needs(route, "checker"):
            assert "FORBIDDEN" in errors, key


def test_errors_appear_in_the_spec_grouped_by_status(client):
    responses = spec(client)["paths"]["/api/bookings"]["post"]["responses"]
    assert set(responses) >= {"201", "401", "403", "404", "409", "422", "429"}
    conflict = responses["409"]["content"]["application/json"]
    assert conflict["examples"]["RIDER_UNAVAILABLE"]["value"]["code"] == "RIDER_UNAVAILABLE"
    # Both shapes of 422 remain visible.
    invalid = responses["422"]["content"]["application/json"]
    assert len(invalid["schema"]["anyOf"]) == 2
    assert "SELF_BOOKING" in invalid["examples"]


def test_login_example_really_logs_in_after_seeding(client, db):
    seed(db)
    example = api_docs.OPERATIONS["POST /api/auth/login"].request
    response = client.post("/api/auth/login", json=example)
    assert response.status_code == 200
    assert response.json()["user"]["roles"] == ["customer", "merchant"]


def test_register_example_really_registers(client):
    example = api_docs.OPERATIONS["POST /api/auth/register"].request
    assert client.post("/api/auth/register", json=example).status_code == 201


def test_examples_contain_no_real_secrets():
    source = Path("app/api_docs.py").read_text()
    assert "sample" in source
    assert not re.search(r"postgresql://|sk-ant-|RPTY", source)


def test_upload_routes_stay_multipart(client):
    body = spec(client)["paths"]["/api/users/me/profile-image"]["put"]["requestBody"]
    assert "multipart/form-data" in body["content"]
