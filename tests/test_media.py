import io
from datetime import timedelta

import pytest
from PIL import Image

from app.adapters import storage
from app.adapters.storage import S3Storage
from app.errors import AppError
from tests.conftest import tomorrow


def png(size=(200, 100)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, "red").save(output, format="PNG")
    return output.getvalue()


def jpeg_with_gps() -> bytes:
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"
    exif.get_ifd(0x8825)[1] = "S"  # GPSLatitudeRef
    output = io.BytesIO()
    Image.new("RGB", (100, 100), "blue").save(output, format="JPEG", exif=exif)
    return output.getvalue()


PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"


def upload_image(client, who, data=None, name="me.png", content_type="image/png"):
    return client.put(
        "/api/users/me/profile-image",
        files={"file": (name, data if data is not None else png(), content_type)},
        headers=who["headers"],
    )


def upload_document(client, who, kind="DRIVING_LICENCE", data=None, name="doc.png"):
    return client.post(
        "/api/riders/me/verification-documents",
        data={"document_type": kind},
        files={"file": (name, data if data is not None else png(), "image/png")},
        headers=who["headers"],
    )


def review(client, admin, document_id, approve=True):
    return client.post(
        f"/api/admin/verification-documents/{document_id}/review",
        json={"approve": approve},
        headers=admin["headers"],
    )


def test_profile_image_appears_everywhere_it_should(world, client, object_store):
    rider = world.rider()
    customer = world.customer()
    response = upload_image(client, rider)
    assert response.status_code == 200
    url = response.json()["profile_image_url"]
    assert url.startswith("https://storage.test/profile-images/")

    assert client.get("/api/auth/me", headers=rider["headers"]).json()["profile_image_url"] == url
    assert client.get("/api/riders").json()[0]["profile_image_url"] == url
    assert client.get(f"/api/riders/{rider['id']}").json()["profile_image_url"] == url
    assert client.get("/api/riders/me", headers=rider["headers"]).json()["profile_image_url"] == url
    booking = world.book(customer, rider).json()
    assert booking["rider"]["profile_image_url"] == url


def test_customers_can_have_a_profile_image_too(world, client):
    customer = world.customer()
    assert upload_image(client, customer).status_code == 200


def test_location_metadata_is_removed_and_large_images_shrink(world, client, object_store):
    customer = world.customer()
    original = jpeg_with_gps()
    assert Image.open(io.BytesIO(original)).getexif()
    upload_image(client, customer, original, "me.jpg", "image/jpeg")
    stored, content_type = next(iter(object_store.objects.values()))
    assert content_type == "image/jpeg"
    assert not Image.open(io.BytesIO(stored)).getexif()

    upload_image(client, customer, png((3000, 1500)))
    stored, _ = next(iter(object_store.objects.values()))
    assert Image.open(io.BytesIO(stored)).size == (1024, 512)


def test_replacing_removes_the_old_file_and_delete_clears_it(world, client, object_store):
    customer = world.customer()
    upload_image(client, customer)
    first = set(object_store.objects)
    upload_image(client, customer)
    assert len(object_store.objects) == 1
    assert set(object_store.objects) != first

    path = "/api/users/me/profile-image"
    assert client.delete(path, headers=customer["headers"]).status_code == 204
    assert object_store.objects == {}
    me = client.get("/api/auth/me", headers=customer["headers"]).json()
    assert me["profile_image_url"] is None
    assert client.delete(path, headers=customer["headers"]).status_code == 404


@pytest.mark.parametrize(
    "data",
    [
        b"<script>alert(1)</script>",
        b"<svg xmlns='http://www.w3.org/2000/svg'/>",
        png()[:40],
        PDF,
    ],
)
def test_non_images_are_refused_whatever_they_claim_to_be(world, client, object_store, data):
    response = upload_image(client, world.customer(), data)
    assert response.status_code == 422
    assert response.json()["code"] == "BAD_IMAGE"
    assert object_store.objects == {}


def test_empty_and_oversized_files_are_refused(world, client, object_store):
    customer = world.customer()
    assert upload_image(client, customer, b"").status_code == 422
    response = upload_image(client, customer, b"x" * (5 * 1024 * 1024 + 1))
    assert response.status_code == 413
    assert object_store.objects == {}


def test_uploads_need_login_and_a_confirmed_phone(world, client):
    assert (
        client.put(
            "/api/users/me/profile-image", files={"file": ("a.png", png(), "image/png")}
        ).status_code
        == 401
    )
    assert upload_image(client, world.register(confirm=False)).status_code == 403


def test_filename_cannot_choose_the_storage_path(world, client, object_store):
    customer = world.customer()
    upload_image(client, customer, name="../../verification-documents/evil.png")
    key = next(iter(object_store.objects))
    assert key.startswith(f"profile-images/{customer['id']}/")
    assert "evil" not in key


def test_missing_storage_gives_503_but_pages_still_load(world, client, monkeypatch):
    rider = world.rider()
    upload_image(client, rider)
    monkeypatch.setattr(storage, "is_configured", lambda: False)

    def missing():
        raise AppError(503, "STORAGE_NOT_CONFIGURED", "File storage is not set up yet")

    monkeypatch.setattr(storage, "get_storage", missing)
    response = upload_image(client, rider)
    assert response.status_code == 503
    assert response.json()["code"] == "STORAGE_NOT_CONFIGURED"
    listed = client.get("/api/riders").json()
    assert listed[0]["profile_image_url"] is None


def test_rider_uploads_a_document_and_sees_only_its_status(world, client, object_store):
    rider = world.rider()
    response = upload_document(client, rider)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "PENDING"
    assert "storage_key" not in body and "url" not in body
    key = next(iter(object_store.objects))
    assert key.startswith(f"verification-documents/{rider['id']}/driving-licence/")

    mine = client.get("/api/riders/me/verification-documents", headers=rider["headers"]).json()
    assert [d["id"] for d in mine] == [body["id"]]
    assert "verification-documents" not in client.get(f"/api/riders/{rider['id']}").text


def test_pdf_is_accepted_and_typed_from_content(world, client, object_store):
    response = upload_document(client, world.rider(), data=PDF, name="scan.exe")
    assert response.json()["content_type"] == "application/pdf"
    key, (_, content_type) = next(iter(object_store.objects.items()))
    assert key.endswith(".pdf")
    assert content_type == "application/pdf"


def test_only_riders_with_a_profile_can_upload_documents(world, client):
    assert upload_document(client, world.customer()).status_code == 403
    no_profile = world.register(("rider",), "Nobody")
    assert upload_document(client, no_profile).json()["code"] == "NO_PROFILE"


def test_bad_document_type_is_refused(world, client):
    assert upload_document(client, world.rider(), kind="PASSPORT").status_code == 422


def test_only_admins_can_open_documents_and_each_view_is_logged(world, client):
    rider = world.rider()
    admin = world.admin()
    document_id = upload_document(client, rider).json()["id"]
    path = f"/api/admin/verification-documents/{document_id}/link"

    assert client.get(path).status_code == 401
    for outsider in (rider, world.customer()):
        assert client.get(path, headers=outsider["headers"]).status_code == 403
    assert (
        client.get("/api/admin/verification-documents", headers=rider["headers"]).status_code == 403
    )

    link = client.get(path, headers=admin["headers"]).json()
    assert link["expires_in_seconds"] == 300
    assert "expires=300" in link["url"]

    events = client.get(
        "/api/admin/audit-events", params={"entity_id": document_id}, headers=admin["headers"]
    ).json()
    assert "document.viewed" in [event["action"] for event in events]


def test_badge_needs_both_latest_documents_approved(world, client):
    rider = world.rider()
    admin = world.admin()
    licence = upload_document(client, rider, "DRIVING_LICENCE").json()["id"]
    card = upload_document(client, rider, "RIDER_CARD").json()["id"]

    queue = client.get("/api/admin/verification-documents", headers=admin["headers"]).json()
    assert {d["id"] for d in queue} == {licence, card}

    first = review(client, admin, licence).json()
    assert first["verification"]["status"] == "PHONE_CONFIRMED"
    second = review(client, admin, card).json()
    assert second["verification"]["label"] == "Documents manually checked"
    profile = client.get(f"/api/riders/{rider['id']}").json()
    assert profile["verification"]["status"] == "DOCUMENTS_MANUALLY_CHECKED"
    assert review(client, admin, card).status_code == 409


def test_rejected_replacement_removes_the_badge(world, client):
    rider = world.rider()
    admin = world.admin()
    review(client, admin, upload_document(client, rider, "DRIVING_LICENCE").json()["id"])
    review(client, admin, upload_document(client, rider, "RIDER_CARD").json()["id"])

    replacement = upload_document(client, rider, "DRIVING_LICENCE").json()["id"]
    result = client.post(
        f"/api/admin/verification-documents/{replacement}/review",
        json={"approve": False, "note": "Licence has expired"},
        headers=admin["headers"],
    ).json()
    assert result["verification"]["status"] == "PHONE_CONFIRMED"
    assert result["document"]["review_note"] == "Licence has expired"
    mine = client.get("/api/riders/me/verification-documents", headers=rider["headers"]).json()
    assert mine[0]["review_note"] == "Licence has expired"


def test_uploading_is_rate_limited(world, client):
    rider = world.rider()
    codes = [upload_document(client, rider).status_code for _ in range(11)]
    assert codes[:10] == [201] * 10
    assert codes[10] == 429


def test_s3_client_uses_path_style_links_for_rumptycloud():
    store = S3Storage(
        "https://s3.rumptycloud.com", "olas-closet", "my-bucket", "AKEXAMPLE", "secret-example"
    )
    url = store.presigned_url("profile-images/a.jpg", 300)
    assert url.startswith("https://s3.rumptycloud.com/my-bucket/profile-images/a.jpg?")
    assert "X-Amz-Expires=300" in url
    assert "olas-closet" in url
    assert "secret-example" not in url


def test_files_do_not_affect_booking_rules(world, client):
    rider = world.rider()
    world.open_slot(rider, tomorrow())
    upload_image(client, rider)
    assert (
        client.get(
            "/api/riders",
            params={"area": world.cbd, "time": (tomorrow() + timedelta(hours=1)).isoformat()},
        ).json()[0]["id"]
        == rider["id"]
    )
