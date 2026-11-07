# Nelp API

The backend for Nelp, a delivery marketplace where customers and merchants choose a
rider by trust score, availability and service area. Built with FastAPI, SQLAlchemy
and PostgreSQL (SQLite works for local development).

## Run it

You need [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
cp .env.example .env
uv sync
uv run python -m app.seed      # optional when SEED_ON_STARTUP=true
uv run fastapi dev app/main.py
```

- Interactive API docs: <http://localhost:8000/docs>. Log in, then paste the token into **Authorize**.
- Tests: `uv run pytest`
- Tests on PostgreSQL, including the race tests: `TEST_DATABASE_URL=postgresql://... uv run pytest`
- API plus PostgreSQL in Docker: `docker compose up --build`

### Demo accounts

These exist after seeding, all with PIN `1234` (`DEMO_PIN`).

| Who | Phone | Notes |
| --- | --- | --- |
| Amina W. (demo) | `0700000001` | Score 89 from 20 ratings, the proposal's example |
| Brian O. (demo) | `0700000002` | Score 83 |
| Chebet K. (demo) | `0700000003` | Score 75 |
| David, Esther, Felix (demo) | `0700000004` to `06` | "New" riders with 1, 2 and 0 ratings |
| Demo Customer 1 to 8 | `0700000101` to `108` | Customer and merchant roles |
| Admin | `ADMIN_PHONE` | Needs `ADMIN_PHONE` and `ADMIN_PIN` set before seeding |

## Front-end guide

**Auth.** Send `Authorization: Bearer <access_token>`, which comes from register or login.

**Errors** have one shape: `{"detail": "Readable message", "code": "MACHINE_CODE"}`.
Validation failures (422) use FastAPI's usual list in `detail`.

**Money** is in integer minor units: `19000` means KES 190.00.

**Times** come back in UTC. Show them in Africa/Nairobi. Times you send must include an
offset, such as `2026-11-08T10:00:00+03:00`. Encode the `+` in query strings.

**Trust display.** Use `trust.label` ("89 · 20 ratings" or "New · 1 rating") as the headline.
For new riders `trust_score` and `average_stars` are `null` on purpose, so never show
them as 0. Always show `is_demo` riders as demo, and show `verification.label` as written.

**Payments are simulated.** While `payment.is_simulated` is true, label it in the UI.

### Profile photos and documents

Send uploads as `multipart/form-data`, with the file in a field named `file`.
The limit is 5 MB.

| What | Who | Call |
| --- | --- | --- |
| Set or replace a photo | Any confirmed user | `PUT /api/users/me/profile-image` |
| Remove the photo | Any confirmed user | `DELETE /api/users/me/profile-image` |
| Upload a licence or rider card | Rider | `POST /api/riders/me/verification-documents` with `document_type` set to `DRIVING_LICENCE` or `RIDER_CARD` |
| Check document status | Rider | `GET /api/riders/me/verification-documents` |
| See the review queue | Admin | `GET /api/admin/verification-documents` |
| Open a document | Admin | `GET /api/admin/verification-documents/{id}/link` |
| Approve or reject | Admin | `POST /api/admin/verification-documents/{id}/review` |

`profile_image_url` appears on users, riders and bookings, and is `null` when there is
no photo. It stops working after an hour, so fetch it fresh rather than saving it.

The "Documents manually checked" badge appears once a person has approved the latest
rider card and licence. If a replacement is rejected, the badge goes away.

### The main flow

| Step | Who | Call |
| --- | --- | --- |
| Sign up | Anyone | `POST /api/auth/register`, then `POST /api/auth/confirm-phone` |
| Rider setup | Rider | `PUT /api/riders/me`, then `PUT /api/riders/me/availability` |
| Pick areas | Customer | `GET /api/areas` and `GET /api/fares/estimate` |
| Compare riders | Customer | `GET /api/riders?area=&time=&sort=trust` and `GET /api/riders/{id}` |
| Book | Customer | `POST /api/bookings`. Omit `scheduled_at` to book now |
| Pay | Customer | `POST /api/payments/intent` with an `Idempotency-Key` header, then `POST /api/payments/{id}/simulate` |
| Respond | Rider | `POST /api/bookings/{id}/accept` or `/decline` |
| Pick up | Rider | `POST /api/bookings/{id}/status` with `{"status": "PICKED_UP"}` |
| Deliver | Rider or customer | `POST /api/bookings/{id}/confirm-delivery` with the recipient's code |
| Rate | Customer, merchant or recipient | `POST /api/bookings/{id}/ratings` when `can_rate` is true |
| Show the books | Booking participant | `GET /api/bookings/{id}/ledger` |

Also useful:

- `GET /api/riders/{id}/reviews` and `GET /api/riders/{id}/explanation`
- `GET /api/bookings` lists your bookings. Filter with `role=` and `status=`.
- `GET /api/track/{token}` powers the SMS link and needs no login.
- `POST /api/ratings/{id}/report` reports a rating.
- `POST /api/bookings/{id}/cancel`. After pickup it needs a `reason` and waits for
  support, so check `cancellation_pending`.
- When `delivery_code_sms` is `FAILED`, offer `POST /api/bookings/{id}/resend-code`.
- `GET /api/demo/inbox?phone=` shows the messages the demo SMS gateway "sent".

Admin routes are under `/api/admin`: verification, cancellation review, rating reports,
abuse flags, trial balance, payouts and the audit log.

## Booking rules

```
REQUESTED -> ACCEPTED -> PICKED_UP -> DELIVERED
    |            |           |
 DECLINED     CANCELLED   CANCELLED (reason plus support review)
```

- A request reserves nothing. Accepting reserves the time, and bookings last 60 minutes
  (`BOOKING_DURATION_MINUTES`).
- "Now" uses the rider's on/off toggle. A future time needs an open slot in the pickup area.
- `DELIVERED` can only be set by a valid recipient code.
- The delivery code goes out by SMS when the rider accepts. It allows 5 wrong tries and 3 resends.

## Project layout

```
app/
  routers/      HTTP only: validate, call a service, shape the response
  services/
    scoring.py    The trust score. Pure functions, no database
    riders.py     Search, availability and reputation statistics
    bookings.py   The state machine
    ratings.py    Who can rate, and reports
    ledger.py     Balanced, append-only postings
    payments.py   Payment lifecycle and what it posts
    flags.py      Abuse patterns for human review
    explanations.py  Optional Claude wording with a fixed template fallback
    media.py      Profile photos and verification documents
  adapters/     SmsGateway, PaymentGateway and ObjectStorage, where provider detail lives
  models.py     Tables
  schemas.py    API contracts
  presenters.py Response building, including the phone privacy rules
  seed.py       Labelled demo data
```

## Before the live demo

**Finish the ThriveVas adapter or stay on the demo inbox.** No public ThriveVas API docs
could be found, so `ThriveVasSms.send()` in `app/adapters/sms.py` deliberately fails until
it is written from the provider's real docs. `POST /api/sms/dlr` will also need
ThriveVas's real report format. Until then keep `SMS_PROVIDER=demo` and label the inbox.

**Set production secrets.** With `ENVIRONMENT=production` the app refuses to start on
development secrets. Generate `SECRET_KEY`, `PAYMENT_WEBHOOK_SECRET` and `SMS_DLR_SECRET`
with `openssl rand -hex 32`, and set `CORS_ORIGINS` and `PUBLIC_BASE_URL` to the front end.

**Set up file storage.** Put `S3_BUCKET`, `S3_ACCESS_KEY_ID` and `S3_SECRET_ACCESS_KEY` in
`.env` locally and in the hosting secret store when deployed. Then run
`uv run python -m app.storage_check`, which tests upload, temporary links, privacy and
delete against the real bucket. Until they are set, uploads answer 503 and everything
else works.

**Deploy.** Build the `Dockerfile`, which listens on `$PORT`. RumptyCloud's own setup still
needs checking against its docs.

## Known limits

- **The demo inbox is public.** Anyone can read codes for any phone at `/api/demo/inbox`.
  That is fine for demo data, but never use it with real customers.
- **Rate limits live in process memory,** so they reset on restart and need Redis or
  similar before running more than one worker.
- **Tables are created at startup** with no migrations. Add Alembic before the schema has
  to change with real data in it.
- **Ledger rows are protected by the ORM, not the database.** A raw SQL statement could
  still change them. Add database triggers before production.
- **Refunds and payouts post immediately** because the simulator always succeeds. A real
  gateway should post them when it confirms settlement.
- **A success callback for a payment already marked FAILED is ignored.** A real gateway
  needs a reconciliation step for this.
- **`LINKED_PHONE` is the only linked-account flag.** Device reuse is not checked, because
  the API collects no device data.
- **Replaced documents stay in the bucket** as a record, and nothing deletes them yet. Agree
  a retention period before real riders upload licences.
- **Document checks are by eye.** "Documents manually checked" means a person looked at
  the upload. It is not provider-backed identity verification.
- **Uploads are fully received before the size check.** Set a request size limit on the
  hosting proxy too.
- **Trust scores are not proof.** They summarise past feedback, and a phone badge is not an
  identity check. The UI should say so.
