"""Request and response contracts for the front end."""

import uuid
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from app.models import (
    AccountType,
    BookingStatus,
    DocumentStatus,
    DocumentType,
    LedgerEvent,
    LedgerSide,
    PaymentStatus,
    Role,
    SmsStatus,
    VerificationStatus,
)
from app.security import normalize_phone

Phone = Annotated[str, AfterValidator(normalize_phone)]
Pin = Annotated[str, StringConstraints(pattern=r"^\d{4,6}$")]
Code = Annotated[str, StringConstraints(pattern=r"^\d{6}$")]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=280)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)]
RatingTag = Literal["punctual", "careful", "communicative"]
Sort = Literal["trust", "availability", "distance"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# Auth


class RegisterIn(BaseModel):
    phone: Phone
    pin: Pin
    display_name: Name
    roles: list[Literal["rider", "customer", "merchant"]] = Field(min_length=1, max_length=3)


class LoginIn(BaseModel):
    phone: Phone
    pin: Pin


class ConfirmPhoneIn(BaseModel):
    code: Code


class UserOut(BaseModel):
    id: uuid.UUID
    phone: str
    display_name: str
    roles: list[Role]
    phone_confirmed: bool
    profile_image_url: str | None = None


class AuthOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
    sms_status: SmsStatus | None = None


class SmsOut(BaseModel):
    sms_status: SmsStatus


# Areas and fares


class AreaOut(ORMModel):
    id: uuid.UUID
    name: str
    city: str


class FareOut(BaseModel):
    distance_km: float
    fare_minor: int
    platform_fee_minor: int
    rider_earning_minor: int
    currency: str
    is_estimate: bool = True


# Riders


class TrustOut(BaseModel):
    """Show `label` as the headline. trust_score is null for new riders."""

    label: str
    is_new: bool
    trust_score: int | None
    average_stars: float | None
    adjusted_rating: float | None
    rated_bookings: int
    completed_deliveries: int
    last_rated_at: AwareDatetime | None
    ratings_last_30_days: int
    tag_counts: dict[str, int]


class VerificationOut(BaseModel):
    status: VerificationStatus
    label: str


class RiderListItem(BaseModel):
    id: uuid.UUID
    display_name: str
    vehicle_type: str
    is_demo: bool
    trust: TrustOut
    verification: VerificationOut
    service_areas: list[AreaOut]
    available_now: bool
    availability_updated_at: AwareDatetime | None
    current_area: AreaOut | None
    approx_distance_km: float | None
    profile_image_url: str | None = Field(
        default=None, description="Expires after an hour. Do not store it."
    )


class RiderDetail(RiderListItem):
    upcoming_slots: list["SlotOut"]


class RiderProfileIn(BaseModel):
    display_name: Name
    vehicle_type: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=2, max_length=40)
    ]
    service_area_ids: list[uuid.UUID] = Field(min_length=1, max_length=30)


class SlotIn(BaseModel):
    service_area_id: uuid.UUID
    starts_at: AwareDatetime
    ends_at: AwareDatetime


class SlotOut(ORMModel):
    id: uuid.UUID
    service_area_id: uuid.UUID
    starts_at: AwareDatetime
    ends_at: AwareDatetime


class AvailabilityIn(BaseModel):
    """Every field is optional. `slots`, when sent, replaces all upcoming open slots."""

    is_available: bool | None = None
    current_area_id: uuid.UUID | None = None
    slots: list[SlotIn] | None = Field(default=None, max_length=50)


class MyRiderOut(RiderDetail):
    balance_minor: int


class PublicReview(BaseModel):
    stars: int
    tags: list[str]
    created_at: AwareDatetime


class MyReview(PublicReview):
    id: uuid.UUID
    comment: str | None
    status: str


class ExplanationOut(BaseModel):
    text: str
    source: Literal["claude", "template"]


# Bookings


class BookingIn(BaseModel):
    rider_id: uuid.UUID
    pickup_area_id: uuid.UUID
    dropoff_area_id: uuid.UUID
    scheduled_at: AwareDatetime | None = Field(default=None, description="Omit to book now")
    recipient_phone: Phone | None = Field(default=None, description="Defaults to your own phone")
    item_notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=280)] | None = (
        None
    )


class ReasonIn(BaseModel):
    reason: ShortText | None = None


class StatusIn(BaseModel):
    status: Literal["PICKED_UP"]


class ConfirmDeliveryIn(BaseModel):
    code: Code


class HistoryOut(ORMModel):
    old_status: BookingStatus | None
    new_status: BookingStatus
    reason: str | None
    created_at: AwareDatetime


class PaymentSummary(BaseModel):
    id: uuid.UUID
    status: PaymentStatus
    amount_minor: int
    currency: str
    is_simulated: bool


class BookingRider(BaseModel):
    id: uuid.UUID
    display_name: str
    is_demo: bool
    profile_image_url: str | None = None


class BookingOut(BaseModel):
    id: uuid.UUID
    status: BookingStatus
    rider: BookingRider
    requester_name: str
    pickup_area: AreaOut
    dropoff_area: AreaOut
    scheduled_at: AwareDatetime
    ends_at: AwareDatetime
    fare_minor: int
    platform_fee_minor: int
    currency: str
    item_notes: str | None
    tracking_url: str
    recipient_phone: str = Field(description="Masked unless you are the requester")
    contact_phones: dict[str, str] = Field(
        description="Shared between participants only while the booking is active"
    )
    delivery_code_sms: SmsStatus | None = Field(description="FAILED means show the resend option")
    cancellation_pending: bool
    cancellation_reason: str | None
    can_rate: bool
    payment: PaymentSummary | None
    history: list[HistoryOut]
    created_at: AwareDatetime


class TrackingOut(BaseModel):
    status: BookingStatus
    rider_name: str
    pickup_area: str
    dropoff_area: str
    scheduled_at: AwareDatetime
    updated_at: AwareDatetime


# Ratings


class RatingIn(BaseModel):
    stars: int = Field(ge=1, le=5)
    tags: list[RatingTag] = Field(default_factory=list, max_length=3)
    comment: ShortText | None = None


class RatingOut(BaseModel):
    id: uuid.UUID
    booking_id: uuid.UUID
    stars: int
    tags: list[str]
    rider_trust: TrustOut


class ReportIn(BaseModel):
    reason: ShortText


class ReportOut(ORMModel):
    id: uuid.UUID
    rating_id: uuid.UUID
    reason: str
    status: str
    created_at: AwareDatetime


# Payments and ledger


class PaymentIntentIn(BaseModel):
    booking_id: uuid.UUID


class PaymentOut(ORMModel):
    id: uuid.UUID
    booking_id: uuid.UUID
    provider: str
    provider_reference: str | None
    amount_minor: int
    currency: str
    status: PaymentStatus
    is_simulated: bool


class SimulateIn(BaseModel):
    outcome: Literal["SUCCEEDED", "FAILED"] = "SUCCEEDED"


class LedgerEntryOut(BaseModel):
    account_type: AccountType
    owner_type: str
    side: LedgerSide
    amount_minor: int


class LedgerTransactionOut(BaseModel):
    id: uuid.UUID
    event_type: LedgerEvent
    created_at: AwareDatetime
    entries: list[LedgerEntryOut]


class BookingLedgerOut(BaseModel):
    transactions: list[LedgerTransactionOut]
    total_debits_minor: int
    total_credits_minor: int
    balanced: bool


class TrialBalanceRow(BaseModel):
    account_type: AccountType
    owner_type: str
    owner_id: str
    currency: str
    debits_minor: int
    credits_minor: int


class TrialBalanceOut(BaseModel):
    accounts: list[TrialBalanceRow]
    total_debits_minor: int
    total_credits_minor: int
    balanced: bool


class SmsDlrIn(BaseModel):
    provider_reference: str
    status: Literal["DELIVERED", "FAILED"]


class InboxMessage(ORMModel):
    phone: str
    body: str
    created_at: AwareDatetime


# Files


class ProfileImageOut(BaseModel):
    profile_image_url: str
    expires_in_seconds: int


class DocumentOut(ORMModel):
    """Deliberately has no storage key or link."""

    id: uuid.UUID
    document_type: DocumentType
    content_type: str
    size_bytes: int
    status: DocumentStatus
    review_note: str | None
    created_at: AwareDatetime


class AdminDocumentOut(DocumentOut):
    rider_id: uuid.UUID
    reviewed_by: uuid.UUID | None
    reviewed_at: AwareDatetime | None


class DocumentLinkOut(BaseModel):
    url: str
    expires_in_seconds: int


class DocumentReviewIn(BaseModel):
    approve: bool
    note: ShortText | None = None


class DocumentReviewOut(BaseModel):
    document: AdminDocumentOut
    verification: VerificationOut


# Admin


class VerificationIn(BaseModel):
    status: Literal["PHONE_CONFIRMED", "DOCUMENTS_MANUALLY_CHECKED"]
    note: ShortText | None = None


class ResolveCancellationIn(BaseModel):
    approve: bool
    note: ShortText | None = None


class ResolveReportIn(BaseModel):
    uphold: bool


class PayoutIn(BaseModel):
    amount_minor: int = Field(gt=0)


class PayoutOut(BaseModel):
    transaction_id: uuid.UUID
    balance_minor: int
    is_simulated: bool


class FlagOut(BaseModel):
    type: str
    rider_id: uuid.UUID
    author_id: uuid.UUID | None
    detail: str


class AuditOut(ORMModel):
    actor_id: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: str
    meta: dict
    created_at: AwareDatetime
