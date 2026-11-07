"""Database tables, following the schema in the technical proposal.

Additions to the proposal's table list, each needed by a stated requirement:
- phone_confirmations: the SMS phone check at registration.
- rating_reports: the basic report/dispute action.
- audit_events: the audit entity from section 8.
- demo_inbox_messages: the SMS fallback. Only the demo adapter writes to it.
- profile_images, verification_documents: files kept in S3. The profile image has
  its own table because startup only creates tables; it cannot add columns.
"""

import enum
import secrets
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, UtcDateTime, utcnow


def _enum(enum_cls):
    return Enum(
        enum_cls,
        native_enum=False,
        length=40,
        values_callable=lambda e: [member.value for member in e],
    )


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(UtcDateTime, default=utcnow)


class Role(enum.StrEnum):
    RIDER = "rider"
    CUSTOMER = "customer"
    MERCHANT = "merchant"
    ADMIN = "admin"


class VerificationStatus(enum.StrEnum):
    UNVERIFIED = "UNVERIFIED"
    PHONE_CONFIRMED = "PHONE_CONFIRMED"
    DOCUMENTS_MANUALLY_CHECKED = "DOCUMENTS_MANUALLY_CHECKED"


class SlotStatus(enum.StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class BookingStatus(enum.StrEnum):
    REQUESTED = "REQUESTED"
    ACCEPTED = "ACCEPTED"
    PICKED_UP = "PICKED_UP"
    DELIVERED = "DELIVERED"
    DECLINED = "DECLINED"
    CANCELLED = "CANCELLED"


class RatingStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    REMOVED = "REMOVED"


class ReportStatus(enum.StrEnum):
    OPEN = "OPEN"
    UPHELD = "UPHELD"
    DISMISSED = "DISMISSED"


class PaymentStatus(enum.StrEnum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


class AccountType(enum.StrEnum):
    PAYMENT_CLEARING = "PAYMENT_CLEARING"
    BOOKING_LIABILITY = "BOOKING_LIABILITY"
    RIDER_PAYABLE = "RIDER_PAYABLE"
    PLATFORM_FEE_PAYABLE = "PLATFORM_FEE_PAYABLE"
    PAYOUT_CLEARING = "PAYOUT_CLEARING"


class LedgerSide(enum.StrEnum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class LedgerEvent(enum.StrEnum):
    PAYMENT_COLLECTED = "PAYMENT_COLLECTED"
    DELIVERY_ALLOCATED = "DELIVERY_ALLOCATED"
    REFUND = "REFUND"
    RIDER_PAYOUT = "RIDER_PAYOUT"


class DocumentType(enum.StrEnum):
    RIDER_CARD = "RIDER_CARD"
    DRIVING_LICENCE = "DRIVING_LICENCE"


class DocumentStatus(enum.StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class SmsType(enum.StrEnum):
    PHONE_CONFIRMATION = "PHONE_CONFIRMATION"
    BOOKING_REQUESTED = "BOOKING_REQUESTED"
    BOOKING_ACCEPTED = "BOOKING_ACCEPTED"
    BOOKING_DECLINED = "BOOKING_DECLINED"
    BOOKING_CANCELLED = "BOOKING_CANCELLED"
    DELIVERY_CODE = "DELIVERY_CODE"


class SmsStatus(enum.StrEnum):
    QUEUED = "QUEUED"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    phone: Mapped[str] = mapped_column(String(16), unique=True)
    pin_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(default=True)
    phone_confirmed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = _created_at()

    role_rows: Mapped[list["UserRole"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def roles(self) -> set[Role]:
        return {row.role for row in self.role_rows}


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[Role] = mapped_column(_enum(Role), primary_key=True)


class PhoneConfirmation(Base):
    __tablename__ = "phone_confirmations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    attempts: Mapped[int] = mapped_column(default=0)
    resend_count: Mapped[int] = mapped_column(default=0)
    sent_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    confirmed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class ServiceArea(Base):
    __tablename__ = "service_areas"
    __table_args__ = (UniqueConstraint("name", "city"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(80))
    city: Mapped[str] = mapped_column(String(80))
    # Coarse neighbourhood centre, used only for approximate distance and fares.
    latitude: Mapped[float]
    longitude: Mapped[float]


class RiderProfile(Base):
    __tablename__ = "rider_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(80))
    vehicle_type: Mapped[str] = mapped_column(String(40))
    verification_status: Mapped[VerificationStatus] = mapped_column(
        _enum(VerificationStatus), default=VerificationStatus.UNVERIFIED
    )
    verification_checked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    verification_checked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    is_available: Mapped[bool] = mapped_column(default=False)
    availability_updated_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    current_area_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("service_areas.id"))
    is_demo: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = _created_at()

    user: Mapped[User] = relationship(foreign_keys=[user_id])
    current_area: Mapped[ServiceArea | None] = relationship()
    service_areas: Mapped[list[ServiceArea]] = relationship(
        secondary="rider_service_areas", lazy="selectin", order_by="ServiceArea.name"
    )


class RiderServiceArea(Base):
    __tablename__ = "rider_service_areas"

    rider_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rider_profiles.user_id"), primary_key=True
    )
    service_area_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("service_areas.id"), primary_key=True
    )


class AvailabilitySlot(Base):
    __tablename__ = "availability_slots"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ck_slot_order"),
        Index("ix_slots_search", "service_area_id", "status", "starts_at", "ends_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    rider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rider_profiles.user_id"))
    service_area_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("service_areas.id"))
    starts_at: Mapped[datetime] = mapped_column(UtcDateTime)
    ends_at: Mapped[datetime] = mapped_column(UtcDateTime)
    status: Mapped[SlotStatus] = mapped_column(_enum(SlotStatus), default=SlotStatus.OPEN)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("fare_minor > 0", name="ck_booking_fare"),
        CheckConstraint("platform_fee_minor >= 0", name="ck_booking_fee"),
        Index("ix_bookings_rider", "rider_id", "status", "scheduled_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    requester_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    recipient_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    recipient_phone: Mapped[str] = mapped_column(String(16))
    rider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rider_profiles.user_id"))
    pickup_area_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("service_areas.id"))
    dropoff_area_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("service_areas.id"))
    scheduled_at: Mapped[datetime] = mapped_column(UtcDateTime)
    ends_at: Mapped[datetime] = mapped_column(UtcDateTime)
    fare_minor: Mapped[int]
    platform_fee_minor: Mapped[int]
    currency: Mapped[str] = mapped_column(String(3), default="KES")
    status: Mapped[BookingStatus] = mapped_column(
        _enum(BookingStatus), default=BookingStatus.REQUESTED
    )
    item_notes: Mapped[str | None] = mapped_column(String(280))
    tracking_token: Mapped[str] = mapped_column(
        String(32), unique=True, default=lambda: secrets.token_urlsafe(16)
    )
    cancellation_reason: Mapped[str | None] = mapped_column(String(280))
    cancellation_requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)

    requester: Mapped[User] = relationship(foreign_keys=[requester_id])
    rider: Mapped[RiderProfile] = relationship(foreign_keys=[rider_id])
    pickup_area: Mapped[ServiceArea] = relationship(foreign_keys=[pickup_area_id])
    dropoff_area: Mapped[ServiceArea] = relationship(foreign_keys=[dropoff_area_id])
    history: Mapped[list["BookingStatusHistory"]] = relationship(
        order_by="BookingStatusHistory.created_at"
    )
    confirmation: Mapped["DeliveryConfirmation | None"] = relationship(uselist=False)
    payments: Mapped[list["Payment"]] = relationship(order_by="Payment.created_at")


class BookingStatusHistory(Base):
    __tablename__ = "booking_status_history"

    id: Mapped[uuid.UUID] = _uuid_pk()
    booking_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bookings.id"), index=True)
    old_status: Mapped[BookingStatus | None] = mapped_column(_enum(BookingStatus))
    new_status: Mapped[BookingStatus] = mapped_column(_enum(BookingStatus))
    changed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str | None] = mapped_column(String(280))
    created_at: Mapped[datetime] = _created_at()


class DeliveryConfirmation(Base):
    __tablename__ = "delivery_confirmations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    booking_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bookings.id"), unique=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    attempts: Mapped[int] = mapped_column(default=0)
    resend_count: Mapped[int] = mapped_column(default=0)
    sent_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    confirmed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class Rating(Base):
    __tablename__ = "ratings"
    __table_args__ = (
        UniqueConstraint("booking_id", "author_id", name="uq_rating_booking_author"),
        CheckConstraint("stars BETWEEN 1 AND 5", name="ck_rating_stars"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    booking_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bookings.id"))
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    stars: Mapped[int] = mapped_column(Integer)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    comment: Mapped[str | None] = mapped_column(String(280))
    status: Mapped[RatingStatus] = mapped_column(_enum(RatingStatus), default=RatingStatus.ACTIVE)
    created_at: Mapped[datetime] = _created_at()

    booking: Mapped[Booking] = relationship()


class RatingReport(Base):
    __tablename__ = "rating_reports"
    __table_args__ = (UniqueConstraint("rating_id", "reporter_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    rating_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ratings.id"), index=True)
    reporter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(String(280))
    status: Mapped[ReportStatus] = mapped_column(_enum(ReportStatus), default=ReportStatus.OPEN)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = _created_at()

    rating: Mapped[Rating] = relationship()


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_payment_amount"),
        # At most one live charge per booking, enforced by the database so that
        # two racing requests cannot both charge the customer.
        Index(
            "uq_payment_live_per_booking",
            "booking_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'SUCCEEDED')"),
            sqlite_where=text("status IN ('PENDING', 'SUCCEEDED')"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    booking_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bookings.id"), index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    provider: Mapped[str] = mapped_column(String(40))
    provider_reference: Mapped[str | None] = mapped_column(String(120), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    amount_minor: Mapped[int]
    currency: Mapped[str] = mapped_column(String(3), default="KES")
    status: Mapped[PaymentStatus] = mapped_column(
        _enum(PaymentStatus), default=PaymentStatus.PENDING
    )
    is_simulated: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class LedgerAccount(Base):
    __tablename__ = "ledger_accounts"
    __table_args__ = (UniqueConstraint("owner_type", "owner_id", "account_type", "currency"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    owner_type: Mapped[str] = mapped_column(String(20))  # "platform" or "rider"
    owner_id: Mapped[str] = mapped_column(String(36))  # "platform" or the rider's id
    account_type: Mapped[AccountType] = mapped_column(_enum(AccountType))
    currency: Mapped[str] = mapped_column(String(3), default="KES")


class LedgerTransaction(Base):
    __tablename__ = "ledger_transactions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    booking_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bookings.id"), index=True)
    payment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("payments.id"))
    event_type: Mapped[LedgerEvent] = mapped_column(_enum(LedgerEvent))
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    created_at: Mapped[datetime] = _created_at()

    entries: Mapped[list["LedgerEntry"]] = relationship(lazy="selectin")


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (CheckConstraint("amount_minor > 0", name="ck_ledger_amount"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ledger_transactions.id"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ledger_accounts.id"), index=True)
    side: Mapped[LedgerSide] = mapped_column(_enum(LedgerSide))
    amount_minor: Mapped[int]
    created_at: Mapped[datetime] = _created_at()

    account: Mapped[LedgerAccount] = relationship(lazy="joined")


def _reject_ledger_change(_mapper, _connection, target) -> None:
    raise ValueError(f"{type(target).__name__} is append-only; post a new transaction instead")


for _model in (LedgerTransaction, LedgerEntry):
    event.listen(_model, "before_update", _reject_ledger_change)
    event.listen(_model, "before_delete", _reject_ledger_change)


class ProfileImage(Base):
    __tablename__ = "profile_images"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    storage_key: Mapped[str] = mapped_column(String(200), unique=True)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class VerificationDocument(Base):
    __tablename__ = "verification_documents"
    __table_args__ = (Index("ix_documents_rider", "rider_id", "document_type", "created_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    rider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rider_profiles.user_id"))
    document_type: Mapped[DocumentType] = mapped_column(_enum(DocumentType))
    storage_key: Mapped[str] = mapped_column(String(200), unique=True)
    content_type: Mapped[str] = mapped_column(String(40))
    size_bytes: Mapped[int]
    status: Mapped[DocumentStatus] = mapped_column(
        _enum(DocumentStatus), default=DocumentStatus.PENDING, index=True
    )
    review_note: Mapped[str | None] = mapped_column(String(280))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = _created_at()


class SmsMessage(Base):
    __tablename__ = "sms_messages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    booking_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bookings.id"), index=True)
    recipient_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    recipient_phone: Mapped[str] = mapped_column(String(16))
    message_type: Mapped[SmsType] = mapped_column(_enum(SmsType))
    provider: Mapped[str] = mapped_column(String(40))
    provider_reference: Mapped[str | None] = mapped_column(String(120), index=True)
    status: Mapped[SmsStatus] = mapped_column(_enum(SmsStatus), default=SmsStatus.QUEUED)
    error: Mapped[str | None] = mapped_column(String(280))
    sent_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = _created_at()


class DemoInboxMessage(Base):
    __tablename__ = "demo_inbox_messages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    phone: Mapped[str] = mapped_column(String(16), index=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _created_at()
