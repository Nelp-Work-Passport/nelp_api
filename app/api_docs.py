"""Examples and error responses for /docs.

Added to the generated spec, so routes stay free of documentation. Tests check
every example against the real models and every error against the source code.
"""

from dataclasses import dataclass, field

from fastapi import FastAPI

DESCRIPTION = """
Backend for Nelp: choose a delivery rider by reputation, availability and service area.

**Getting started**

1. Send `POST /api/auth/login`. Its example logs in the demo customer; the demo rider is
   `0700000001`. Both use PIN `1234`.
2. Click **Authorize** and paste the `access_token`.
3. Get real ids from `GET /api/areas` and `GET /api/riders`. **The ids in these examples are
   samples**, so replace them, because each database has its own.

**Conventions**

- Errors look like `{"detail": "Readable message", "code": "MACHINE_CODE"}`. Branch on `code`.
- Validation errors (422) from FastAPI have a list in `detail` and no `code`.
- Money is in integer minor units: `19000` means KES 190.00.
- Times come back in UTC. Times you send need an offset, such as `+03:00`.
- For new riders `trust_score` and `average_stars` are `null`. Show `trust.label`, not 0.
"""

# code: (HTTP status, example message)
ERRORS: dict[str, tuple[int, str]] = {
    "UNAUTHENTICATED": (401, "Log in to continue"),
    "FORBIDDEN": (403, "You are not allowed to do that"),
    "NOT_FOUND": (404, "Booking not found"),
    "RATE_LIMITED": (429, "Too many attempts. Please wait and try again."),
    "PHONE_TAKEN": (409, "This phone number is already registered"),
    "BAD_CREDENTIALS": (401, "Phone number or PIN is wrong"),
    "WRONG_CODE": (400, "Wrong code. 4 attempts left."),
    "CODE_EXPIRED": (410, "This code has expired. Request a new one."),
    "CODE_LOCKED": (429, "Too many wrong codes. Contact support."),
    "NO_CODE": (409, "Request a new code"),
    "ALREADY_CONFIRMED": (409, "This phone is already confirmed"),
    "RESEND_LIMIT": (429, "This code cannot be resent again. Contact support."),
    "BAD_IMAGE": (422, "Upload a JPEG, PNG or WebP image"),
    "EMPTY_FILE": (422, "The file is empty"),
    "FILE_TOO_LARGE": (413, "Files can be at most 5 MB"),
    "STORAGE_ERROR": (502, "The file could not be saved"),
    "STORAGE_NOT_CONFIGURED": (503, "File storage is not set up yet"),
    "NO_PROFILE": (404, "Create your rider profile first"),
    "ALREADY_REVIEWED": (409, "This has already been reviewed"),
    "SLOT_IN_PAST": (422, "Time slots must end in the future"),
    "AREA_NOT_SERVED": (422, "Add this area to your profile first"),
    "SLOTS_OVERLAP": (422, "Time slots must not overlap"),
    "RIDER_UNAVAILABLE": (409, "This rider is not available for that area and time"),
    "SELF_BOOKING": (422, "You cannot book yourself"),
    "TIME_IN_PAST": (422, "Choose a time in the future"),
    "TIME_TOO_FAR": (422, "Book at most 30 days ahead"),
    "INVALID_TRANSITION": (409, "This cannot be done to a DELIVERED booking"),
    "SLOT_TAKEN": (409, "You already have a booking at this time"),
    "REASON_REQUIRED": (422, "Give a reason to cancel after pickup"),
    "CANCELLATION_PENDING": (409, "A cancellation is already under review"),
    "NO_PENDING_CANCELLATION": (409, "No cancellation is waiting for review"),
    "NOT_RATEABLE": (409, "Only a delivered booking can be rated"),
    "ALREADY_RATED": (409, "You have already rated this booking"),
    "ALREADY_REPORTED": (409, "You have already reported this rating"),
    "BOOKING_NOT_PAYABLE": (409, "A CANCELLED booking cannot be paid"),
    "IDEMPOTENCY_KEY_REUSED": (409, "This idempotency key was already used"),
    "PAYMENT_EXISTS": (409, "This booking already has a live payment"),
    "PAYMENT_PROVIDER_ERROR": (502, "The payment could not start"),
    "INSUFFICIENT_BALANCE": (409, "The rider's balance is lower than this payout"),
    "BAD_SIGNATURE": (401, "Webhook signature is invalid"),
    "BAD_WEBHOOK": (400, "Webhook payload is invalid"),
    "AMOUNT_MISMATCH": (422, "Amount does not match the payment"),
    "BAD_SECRET": (401, "Invalid delivery report secret"),
    "BAD_PHONE": (422, "Enter a valid Kenyan phone number, such as 0712345678"),
    "DATABASE_DOWN": (503, "The database is not reachable"),
}

USER = "UNAUTHENTICATED"
ROLE = (USER, "FORBIDDEN")
UPLOAD = ("BAD_IMAGE", "EMPTY_FILE", "FILE_TOO_LARGE", "RATE_LIMITED")
STORAGE = ("STORAGE_ERROR", "STORAGE_NOT_CONFIGURED")

# Sample ids, used consistently so the examples read as one story.
CBD = "1b3006d1-5f3c-4a53-9c1e-3f0a7c2d9e10"
WESTLANDS = "29ab2e3f-f845-436b-b9bd-d04e69d008f0"
CUSTOMER = "5d8c1e24-7a90-4f61-8b2d-c41e90f3a7b2"
RIDER = "93f84092-ee23-42bc-9032-684b3261c27b"
BOOKING = "a4e1c6b7-2d38-49f0-8e15-6b7d3c9f1e20"
PAYMENT = "c7f2a910-4b6d-4e83-a1c5-8d2e6f0b3a47"
RATING = "e2b95d03-6c17-4a8e-b3f4-1a9c7d5e2f68"
REPORT = "f18a3c76-9e42-4d05-a6b1-7c3e0d8b5f92"
DOCUMENT = "b6d4e8f1-3a27-4c59-91e6-2f5a8c0d7e34"
SLOT = "d3a7f5c2-8b14-46e9-b702-9e1c4f6a2d58"
TRANSACTION = "07c5e3a9-1f6d-4b82-9d3e-5a8f2c7b1e46"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.sample.signature"
IMAGE_URL = (
    "https://s3.rumptycloud.com/assets-nelp/profile-images/photo.jpg"
    "?X-Amz-Expires=3600&X-Amz-Signature=sample"
)

AREA_CBD = {"id": CBD, "name": "CBD", "city": "Nairobi"}
AREA_WESTLANDS = {"id": WESTLANDS, "name": "Westlands", "city": "Nairobi"}

USER_OUT = {
    "id": CUSTOMER,
    "phone": "+254700000101",
    "display_name": "Demo Customer 1",
    "roles": ["customer", "merchant"],
    "phone_confirmed": True,
    "profile_image_url": None,
}
AUTH_OUT = {"access_token": TOKEN, "token_type": "bearer", "user": USER_OUT, "sms_status": None}

TRUST = {
    "label": "89 · 20 ratings",
    "is_new": False,
    "trust_score": 89,
    "average_stars": 4.7,
    "adjusted_rating": 4.46,
    "rated_bookings": 20,
    "completed_deliveries": 20,
    "last_rated_at": "2026-11-05T10:12:00Z",
    "ratings_last_30_days": 4,
    "tag_counts": {"punctual": 20},
}
RIDER_ITEM = {
    "id": RIDER,
    "display_name": "Amina W. (demo)",
    "vehicle_type": "Motorbike",
    "is_demo": True,
    "trust": TRUST,
    "verification": {"status": "PHONE_CONFIRMED", "label": "Phone confirmed"},
    "service_areas": [AREA_CBD, AREA_WESTLANDS],
    "available_now": True,
    "availability_updated_at": "2026-11-07T06:00:00Z",
    "current_area": AREA_WESTLANDS,
    "approx_distance_km": 2.2,
    "profile_image_url": IMAGE_URL,
}
SLOT_OUT = {
    "id": SLOT,
    "service_area_id": CBD,
    "starts_at": "2026-11-08T04:00:00Z",
    "ends_at": "2026-11-08T18:00:00Z",
}
RIDER_DETAIL = {**RIDER_ITEM, "approx_distance_km": None, "upcoming_slots": [SLOT_OUT]}
MY_RIDER = {**RIDER_DETAIL, "balance_minor": 17100}

PAYMENT_SUMMARY = {
    "id": PAYMENT,
    "status": "SUCCEEDED",
    "amount_minor": 19000,
    "currency": "KES",
    "is_simulated": True,
}
BOOKING_OUT = {
    "id": BOOKING,
    "status": "ACCEPTED",
    "rider": {
        "id": RIDER,
        "display_name": "Amina W. (demo)",
        "is_demo": True,
        "profile_image_url": IMAGE_URL,
    },
    "requester_name": "Demo Customer 1",
    "pickup_area": AREA_CBD,
    "dropoff_area": AREA_WESTLANDS,
    "scheduled_at": "2026-11-08T07:00:00Z",
    "ends_at": "2026-11-08T08:00:00Z",
    "fare_minor": 19000,
    "platform_fee_minor": 1900,
    "currency": "KES",
    "item_notes": "Small box, handle with care",
    "tracking_url": "https://nelp.example/t/tJ3hjPgHRkw_QR2ymiH65g",
    "recipient_phone": "+254700000101",
    "contact_phones": {"rider": "+254700000001"},
    "delivery_code_sms": "SENT",
    "cancellation_pending": False,
    "cancellation_reason": None,
    "can_rate": False,
    "payment": PAYMENT_SUMMARY,
    "history": [
        {
            "old_status": None,
            "new_status": "REQUESTED",
            "reason": None,
            "created_at": "2026-11-07T15:00:00Z",
        },
        {
            "old_status": "REQUESTED",
            "new_status": "ACCEPTED",
            "reason": None,
            "created_at": "2026-11-07T15:05:00Z",
        },
    ],
    "created_at": "2026-11-07T15:00:00Z",
}
BOOKING_REQUESTED = {
    **BOOKING_OUT,
    "status": "REQUESTED",
    "contact_phones": {},
    "delivery_code_sms": None,
    "payment": None,
    "history": BOOKING_OUT["history"][:1],
}
BOOKING_PICKED_UP = {
    **BOOKING_OUT,
    "status": "PICKED_UP",
    "history": [
        *BOOKING_OUT["history"],
        {
            "old_status": "ACCEPTED",
            "new_status": "PICKED_UP",
            "reason": None,
            "created_at": "2026-11-08T07:02:00Z",
        },
    ],
}
BOOKING_DELIVERED = {
    **BOOKING_PICKED_UP,
    "status": "DELIVERED",
    "contact_phones": {},
    "can_rate": True,
    "history": [
        *BOOKING_PICKED_UP["history"],
        {
            "old_status": "PICKED_UP",
            "new_status": "DELIVERED",
            "reason": None,
            "created_at": "2026-11-08T07:40:00Z",
        },
    ],
}
BOOKING_CANCELLED = {
    **BOOKING_REQUESTED,
    "status": "CANCELLED",
    "cancellation_reason": "Plans changed",
    "history": [
        *BOOKING_REQUESTED["history"],
        {
            "old_status": "REQUESTED",
            "new_status": "CANCELLED",
            "reason": "Plans changed",
            "created_at": "2026-11-07T15:10:00Z",
        },
    ],
}
BOOKING_DECLINED = {
    **BOOKING_REQUESTED,
    "status": "DECLINED",
    "history": [
        *BOOKING_REQUESTED["history"],
        {
            "old_status": "REQUESTED",
            "new_status": "DECLINED",
            "reason": "Bike in for repairs",
            "created_at": "2026-11-07T15:06:00Z",
        },
    ],
}
BOOKING_REVIEW = {
    **BOOKING_PICKED_UP,
    "cancellation_pending": True,
    "cancellation_reason": "Wrong item collected",
    "contact_phones": {},
}

PAYMENT_OUT = {
    "id": PAYMENT,
    "booking_id": BOOKING,
    "provider": "simulated",
    "provider_reference": "sim-3f1d8a52-0c6e-4b7f-9a21-7e5d4c3b2a10",
    "amount_minor": 19000,
    "currency": "KES",
    "status": "PENDING",
    "is_simulated": True,
}
LEDGER_OUT = {
    "transactions": [
        {
            "id": TRANSACTION,
            "event_type": "PAYMENT_COLLECTED",
            "created_at": "2026-11-07T15:02:00Z",
            "entries": [
                {
                    "account_type": "PAYMENT_CLEARING",
                    "owner_type": "platform",
                    "side": "DEBIT",
                    "amount_minor": 19000,
                },
                {
                    "account_type": "BOOKING_LIABILITY",
                    "owner_type": "platform",
                    "side": "CREDIT",
                    "amount_minor": 19000,
                },
            ],
        },
        {
            "id": "1e9b7c35-4d02-4f6a-8e71-b3c6d9a5f024",
            "event_type": "DELIVERY_ALLOCATED",
            "created_at": "2026-11-08T07:40:00Z",
            "entries": [
                {
                    "account_type": "BOOKING_LIABILITY",
                    "owner_type": "platform",
                    "side": "DEBIT",
                    "amount_minor": 19000,
                },
                {
                    "account_type": "RIDER_PAYABLE",
                    "owner_type": "rider",
                    "side": "CREDIT",
                    "amount_minor": 17100,
                },
                {
                    "account_type": "PLATFORM_FEE_PAYABLE",
                    "owner_type": "platform",
                    "side": "CREDIT",
                    "amount_minor": 1900,
                },
            ],
        },
    ],
    "total_debits_minor": 38000,
    "total_credits_minor": 38000,
    "balanced": True,
}
DOCUMENT_OUT = {
    "id": DOCUMENT,
    "document_type": "DRIVING_LICENCE",
    "content_type": "image/jpeg",
    "size_bytes": 482133,
    "status": "PENDING",
    "review_note": None,
    "created_at": "2026-11-07T14:00:00Z",
}
ADMIN_DOCUMENT = {**DOCUMENT_OUT, "rider_id": RIDER, "reviewed_by": None, "reviewed_at": None}
REPORT_OUT = {
    "id": REPORT,
    "rating_id": RATING,
    "reason": "This delivery was on time and nothing was damaged",
    "status": "OPEN",
    "created_at": "2026-11-08T09:00:00Z",
}
PUBLIC_REVIEW = {"stars": 5, "tags": ["punctual"], "created_at": "2026-11-08T08:00:00Z"}


@dataclass(frozen=True)
class Op:
    request: dict | None = None
    response: dict | list | None = None
    errors: tuple[str, ...] = ()
    variants: dict[str, dict] = field(default_factory=dict)  # named request examples


OPERATIONS: dict[str, Op] = {
    "GET /health": Op(
        response={
            "status": "ok",
            "sms_provider": "demo",
            "payments_simulated": True,
            "storage_configured": True,
        },
        errors=("DATABASE_DOWN",),
    ),
    "GET /api/areas": Op(response=[AREA_CBD, AREA_WESTLANDS]),
    "GET /api/fares/estimate": Op(
        response={
            "distance_km": 2.2,
            "fare_minor": 19000,
            "platform_fee_minor": 1900,
            "rider_earning_minor": 17100,
            "currency": "KES",
            "is_estimate": True,
        },
        errors=("NOT_FOUND",),
    ),
    "POST /api/auth/register": Op(
        request={
            "phone": "0712345678",
            "pin": "4821",
            "display_name": "Wanjiku Mwangi",
            "roles": ["customer"],
        },
        response={
            **AUTH_OUT,
            "user": {
                **USER_OUT,
                "phone": "+254712345678",
                "display_name": "Wanjiku Mwangi",
                "roles": ["customer"],
                "phone_confirmed": False,
            },
            "sms_status": "SENT",
        },
        errors=("PHONE_TAKEN", "RATE_LIMITED"),
    ),
    "POST /api/auth/login": Op(
        request={"phone": "0700000101", "pin": "1234"},
        response=AUTH_OUT,
        errors=("BAD_CREDENTIALS", "RATE_LIMITED"),
    ),
    "GET /api/auth/me": Op(response=USER_OUT, errors=(USER,)),
    "POST /api/auth/confirm-phone": Op(
        request={"code": "287794"},
        response=USER_OUT,
        errors=(USER, "WRONG_CODE", "CODE_EXPIRED", "CODE_LOCKED", "NO_CODE"),
    ),
    "POST /api/auth/resend-code": Op(
        response={"sms_status": "SENT"},
        errors=(USER, "ALREADY_CONFIRMED", "RATE_LIMITED", "RESEND_LIMIT"),
    ),
    "PUT /api/users/me/profile-image": Op(
        response={"profile_image_url": IMAGE_URL, "expires_in_seconds": 3600},
        errors=(*ROLE, *UPLOAD, *STORAGE),
    ),
    "DELETE /api/users/me/profile-image": Op(errors=(USER, "NOT_FOUND")),
    "GET /api/riders/me/verification-documents": Op(response=[DOCUMENT_OUT], errors=ROLE),
    "POST /api/riders/me/verification-documents": Op(
        response=DOCUMENT_OUT, errors=(*ROLE, "NO_PROFILE", *UPLOAD, *STORAGE)
    ),
    "GET /api/admin/verification-documents": Op(response=[ADMIN_DOCUMENT], errors=ROLE),
    "GET /api/admin/verification-documents/{document_id}/link": Op(
        response={
            "url": "https://s3.rumptycloud.com/assets-nelp/verification-documents/doc.jpg"
            "?X-Amz-Expires=300&X-Amz-Signature=sample",
            "expires_in_seconds": 300,
        },
        errors=(*ROLE, "NOT_FOUND", "STORAGE_NOT_CONFIGURED"),
    ),
    "POST /api/admin/verification-documents/{document_id}/review": Op(
        request={"approve": True, "note": "Licence is valid and readable"},
        response={
            "document": {
                **ADMIN_DOCUMENT,
                "status": "APPROVED",
                "review_note": "Licence is valid and readable",
                "reviewed_by": CUSTOMER,
                "reviewed_at": "2026-11-07T16:00:00Z",
            },
            "verification": {
                "status": "DOCUMENTS_MANUALLY_CHECKED",
                "label": "Documents manually checked",
            },
        },
        errors=(*ROLE, "NOT_FOUND", "ALREADY_REVIEWED"),
    ),
    "GET /api/riders": Op(response=[RIDER_ITEM], errors=("NOT_FOUND",)),
    "GET /api/riders/me": Op(response=MY_RIDER, errors=(*ROLE, "NO_PROFILE")),
    "PUT /api/riders/me": Op(
        request={
            "display_name": "Amina Wanjiru",
            "vehicle_type": "Motorbike",
            "service_area_ids": [CBD, WESTLANDS],
        },
        response=MY_RIDER,
        errors=(*ROLE, "NOT_FOUND"),
    ),
    "PUT /api/riders/me/availability": Op(
        request={
            "is_available": True,
            "current_area_id": WESTLANDS,
            "slots": [
                {
                    "service_area_id": CBD,
                    "starts_at": "2026-11-08T08:00:00+03:00",
                    "ends_at": "2026-11-08T17:00:00+03:00",
                }
            ],
        },
        response=MY_RIDER,
        errors=(
            *ROLE,
            "NO_PROFILE",
            "NOT_FOUND",
            "SLOT_IN_PAST",
            "AREA_NOT_SERVED",
            "SLOTS_OVERLAP",
        ),
    ),
    "GET /api/riders/me/reviews": Op(
        response=[{**PUBLIC_REVIEW, "id": RATING, "comment": "Very careful", "status": "ACTIVE"}],
        errors=ROLE,
    ),
    "GET /api/riders/{rider_id}": Op(response=RIDER_DETAIL, errors=("NOT_FOUND",)),
    "GET /api/riders/{rider_id}/reviews": Op(response=[PUBLIC_REVIEW], errors=("NOT_FOUND",)),
    "GET /api/riders/{rider_id}/explanation": Op(
        response={
            "text": "This rider scores 89 out of 100 from 20 rated deliveries averaging 4.7 "
            "stars, with 4 ratings in the last 30 days. The score summarises past "
            "feedback and does not guarantee a future delivery.",
            "source": "template",
        },
        errors=("NOT_FOUND",),
    ),
    "POST /api/bookings": Op(
        variants={
            "Book for a set time": {
                "rider_id": RIDER,
                "pickup_area_id": CBD,
                "dropoff_area_id": WESTLANDS,
                "scheduled_at": "2026-11-08T10:00:00+03:00",
                "recipient_phone": "0700000102",
                "item_notes": "Small box, handle with care",
            },
            "Book now, delivering to yourself": {
                "rider_id": RIDER,
                "pickup_area_id": CBD,
                "dropoff_area_id": WESTLANDS,
                "item_notes": "Small box, handle with care",
            },
        },
        response=BOOKING_REQUESTED,
        errors=(
            *ROLE,
            "NOT_FOUND",
            "RIDER_UNAVAILABLE",
            "SELF_BOOKING",
            "TIME_IN_PAST",
            "TIME_TOO_FAR",
            "RATE_LIMITED",
        ),
    ),
    "GET /api/bookings": Op(response=[BOOKING_OUT], errors=(USER,)),
    "GET /api/bookings/{booking_id}": Op(response=BOOKING_OUT, errors=(USER, "NOT_FOUND")),
    "POST /api/bookings/{booking_id}/accept": Op(
        response=BOOKING_OUT, errors=(*ROLE, "NOT_FOUND", "INVALID_TRANSITION", "SLOT_TAKEN")
    ),
    "POST /api/bookings/{booking_id}/decline": Op(
        request={"reason": "Bike in for repairs"},
        response=BOOKING_DECLINED,
        errors=(*ROLE, "NOT_FOUND", "INVALID_TRANSITION"),
    ),
    "POST /api/bookings/{booking_id}/status": Op(
        request={"status": "PICKED_UP"},
        response=BOOKING_PICKED_UP,
        errors=(*ROLE, "NOT_FOUND", "INVALID_TRANSITION"),
    ),
    "POST /api/bookings/{booking_id}/cancel": Op(
        request={"reason": "Plans changed"},
        response=BOOKING_CANCELLED,
        errors=(
            USER,
            "NOT_FOUND",
            "INVALID_TRANSITION",
            "REASON_REQUIRED",
            "CANCELLATION_PENDING",
        ),
    ),
    "POST /api/bookings/{booking_id}/confirm-delivery": Op(
        request={"code": "287794"},
        response=BOOKING_DELIVERED,
        errors=(
            USER,
            "NOT_FOUND",
            "INVALID_TRANSITION",
            "WRONG_CODE",
            "CODE_EXPIRED",
            "CODE_LOCKED",
        ),
    ),
    "POST /api/bookings/{booking_id}/resend-code": Op(
        response=BOOKING_OUT,
        errors=(USER, "NOT_FOUND", "INVALID_TRANSITION", "RATE_LIMITED", "RESEND_LIMIT"),
    ),
    "POST /api/bookings/{booking_id}/ratings": Op(
        request={"stars": 5, "tags": ["punctual", "careful"], "comment": "Very careful"},
        response={
            "id": RATING,
            "booking_id": BOOKING,
            "stars": 5,
            "tags": ["punctual", "careful"],
            "rider_trust": {
                **TRUST,
                "label": "90 · 21 ratings",
                "trust_score": 90,
                "rated_bookings": 21,
                "completed_deliveries": 21,
            },
        },
        errors=(*ROLE, "NOT_FOUND", "NOT_RATEABLE", "ALREADY_RATED", "RATE_LIMITED"),
    ),
    "GET /api/bookings/{booking_id}/ledger": Op(response=LEDGER_OUT, errors=(USER, "NOT_FOUND")),
    "POST /api/ratings/{rating_id}/report": Op(
        request={"reason": "This delivery was on time and nothing was damaged"},
        response=REPORT_OUT,
        errors=(USER, "NOT_FOUND", "ALREADY_REPORTED"),
    ),
    "POST /api/payments/intent": Op(
        request={"booking_id": BOOKING},
        response=PAYMENT_OUT,
        errors=(
            *ROLE,
            "NOT_FOUND",
            "BOOKING_NOT_PAYABLE",
            "IDEMPOTENCY_KEY_REUSED",
            "PAYMENT_EXISTS",
            "PAYMENT_PROVIDER_ERROR",
        ),
    ),
    "POST /api/payments/webhook": Op(
        request={
            "provider_reference": PAYMENT_OUT["provider_reference"],
            "status": "SUCCEEDED",
            "amount_minor": 19000,
            "currency": "KES",
        },
        response={**PAYMENT_OUT, "status": "SUCCEEDED"},
        errors=("BAD_SIGNATURE", "BAD_WEBHOOK", "NOT_FOUND", "AMOUNT_MISMATCH"),
    ),
    "POST /api/payments/{payment_id}/simulate": Op(
        request={"outcome": "SUCCEEDED"},
        response={**PAYMENT_OUT, "status": "SUCCEEDED"},
        errors=(*ROLE, "NOT_FOUND"),
    ),
    "GET /api/track/{token}": Op(
        response={
            "status": "ACCEPTED",
            "rider_name": "Amina W. (demo)",
            "pickup_area": "CBD",
            "dropoff_area": "Westlands",
            "scheduled_at": "2026-11-08T07:00:00Z",
            "updated_at": "2026-11-07T15:05:00Z",
        },
        errors=("NOT_FOUND",),
    ),
    "POST /api/sms/dlr": Op(
        request={"provider_reference": "demo-7a1c9e42", "status": "DELIVERED"},
        response={"status": "DELIVERED"},
        errors=("BAD_SECRET", "NOT_FOUND"),
    ),
    "GET /api/demo/inbox": Op(
        response=[
            {
                "phone": "+254700000101",
                "body": "Nelp: A delivery with Amina W. (demo) is booked for 08 Nov, 10:00 AM. "
                "Confirm details: https://nelp.example/t/tJ3hjPgHRkw_QR2ymiH65g. Code: 287794",
                "created_at": "2026-11-07T15:05:00Z",
            }
        ],
        errors=("NOT_FOUND", "BAD_PHONE"),
    ),
    "POST /api/admin/riders/{rider_id}/verification": Op(
        request={"status": "DOCUMENTS_MANUALLY_CHECKED", "note": "Checked licence in person"},
        response={
            "status": "DOCUMENTS_MANUALLY_CHECKED",
            "label": "Documents manually checked",
        },
        errors=(*ROLE, "NOT_FOUND"),
    ),
    "GET /api/admin/cancellations": Op(response=[BOOKING_REVIEW], errors=ROLE),
    "POST /api/admin/bookings/{booking_id}/resolve-cancellation": Op(
        request={"approve": True, "note": "Customer confirmed the wrong item"},
        response={
            **BOOKING_REVIEW,
            "status": "CANCELLED",
            "cancellation_pending": False,
            "payment": {**PAYMENT_SUMMARY, "status": "REFUNDED"},
        },
        errors=(*ROLE, "NOT_FOUND", "NO_PENDING_CANCELLATION"),
    ),
    "GET /api/admin/rating-reports": Op(response=[REPORT_OUT], errors=ROLE),
    "POST /api/admin/rating-reports/{report_id}/resolve": Op(
        request={"uphold": True},
        response={**REPORT_OUT, "status": "UPHELD"},
        errors=(*ROLE, "NOT_FOUND", "ALREADY_REVIEWED"),
    ),
    "GET /api/admin/flags": Op(
        response=[
            {
                "type": "REPEATED_PAIR",
                "rider_id": RIDER,
                "author_id": CUSTOMER,
                "detail": "3 ratings from one account in 30 days",
            }
        ],
        errors=ROLE,
    ),
    "GET /api/admin/ledger/trial-balance": Op(
        response={
            "accounts": [
                {
                    "account_type": "PAYMENT_CLEARING",
                    "owner_type": "platform",
                    "owner_id": "platform",
                    "currency": "KES",
                    "debits_minor": 19000,
                    "credits_minor": 0,
                },
                {
                    "account_type": "BOOKING_LIABILITY",
                    "owner_type": "platform",
                    "owner_id": "platform",
                    "currency": "KES",
                    "debits_minor": 0,
                    "credits_minor": 19000,
                },
            ],
            "total_debits_minor": 19000,
            "total_credits_minor": 19000,
            "balanced": True,
        },
        errors=ROLE,
    ),
    "POST /api/admin/riders/{rider_id}/payouts": Op(
        request={"amount_minor": 17100},
        response={"transaction_id": TRANSACTION, "balance_minor": 0, "is_simulated": True},
        errors=(*ROLE, "NOT_FOUND", "INSUFFICIENT_BALANCE"),
    ),
    "GET /api/admin/audit-events": Op(
        response=[
            {
                "actor_id": RIDER,
                "action": "booking.status_changed",
                "entity_type": "booking",
                "entity_id": BOOKING,
                "meta": {"old": "REQUESTED", "new": "ACCEPTED"},
                "created_at": "2026-11-07T15:05:00Z",
            }
        ],
        errors=ROLE,
    ),
}

ERROR_SCHEMA = {
    "title": "Error",
    "type": "object",
    "required": ["detail", "code"],
    "properties": {
        "detail": {"type": "string", "description": "A message you can show to the user"},
        "code": {"type": "string", "description": "A stable code to branch on"},
    },
}
STATUS_TEXT = {
    400: "The code or payload is wrong",
    401: "Not logged in, or the credentials are wrong",
    403: "Logged in, but not allowed to do this",
    404: "Not found",
    409: "Conflicts with the current state",
    410: "This code has expired",
    413: "The file is too large",
    422: "The request failed a business rule or validation",
    429: "Too many attempts",
    502: "A provider failed",
    503: "A required service is unavailable",
}


def _add_errors(operation: dict, codes: tuple[str, ...]) -> None:
    by_status: dict[int, list[str]] = {}
    for code in codes:
        by_status.setdefault(ERRORS[code][0], []).append(code)

    for status, status_codes in sorted(by_status.items()):
        examples = {
            code: {"summary": code, "value": {"detail": ERRORS[code][1], "code": code}}
            for code in status_codes
        }
        error_ref = {"$ref": "#/components/schemas/Error"}
        existing = operation["responses"].get(str(status))
        if existing:  # FastAPI's validation error shares 422 with our own errors
            schema = existing["content"]["application/json"]["schema"]
            operation["responses"][str(status)] = {
                "description": STATUS_TEXT[status],
                "content": {
                    "application/json": {
                        "schema": {"anyOf": [schema, error_ref]},
                        "examples": examples,
                    }
                },
            }
        else:
            operation["responses"][str(status)] = {
                "description": STATUS_TEXT[status],
                "content": {"application/json": {"schema": error_ref, "examples": examples}},
            }


def _add_request(operation: dict, op: Op) -> None:
    if op.request is None and not op.variants:
        return
    # The webhook reads raw bytes to check the signature, so it has no generated body.
    body = operation.setdefault(
        "requestBody", {"content": {"application/json": {"schema": {"type": "object"}}}}
    )
    media = body["content"]["application/json"]
    if op.variants:
        media["examples"] = {
            name: {"summary": name, "value": value} for name, value in op.variants.items()
        }
    else:
        media["example"] = op.request


def _add_response(operation: dict, op: Op) -> None:
    if op.response is None:
        return
    success = next(code for code in operation["responses"] if code.startswith("2"))
    operation["responses"][success]["content"]["application/json"]["example"] = op.response


def enrich(spec: dict) -> dict:
    spec["components"]["schemas"]["Error"] = ERROR_SCHEMA
    for path, methods in spec["paths"].items():
        for method, operation in methods.items():
            op = OPERATIONS.get(f"{method.upper()} {path}")
            if op is None:
                continue
            _add_request(operation, op)
            _add_response(operation, op)
            _add_errors(operation, op.errors)
    return spec


def install(app: FastAPI) -> None:
    generate = app.openapi

    def openapi() -> dict:
        if app.openapi_schema is None:
            app.openapi_schema = enrich(generate())
        return app.openapi_schema

    app.openapi = openapi
