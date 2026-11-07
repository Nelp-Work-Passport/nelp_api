"""Payment lifecycle, and the ledger postings each step produces."""

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.payments import ProviderEvent, get_payment_gateway
from app.errors import AppError, conflict, forbidden, not_found
from app.models import (
    AccountType,
    Booking,
    BookingStatus,
    LedgerEvent,
    LedgerSide,
    Payment,
    PaymentStatus,
    User,
)
from app.services import audit, ledger
from app.services.ledger import Line

PAYABLE_STATUSES = (
    BookingStatus.REQUESTED,
    BookingStatus.ACCEPTED,
    BookingStatus.PICKED_UP,
    BookingStatus.DELIVERED,
)
UNFULFILLED_STATUSES = (BookingStatus.CANCELLED, BookingStatus.DECLINED)


def create_intent(db: Session, user: User, booking_id: uuid.UUID, key: str) -> Payment:
    existing = db.scalar(select(Payment).where(Payment.idempotency_key == key))
    if existing:
        if existing.booking_id != booking_id or existing.created_by != user.id:
            raise conflict("IDEMPOTENCY_KEY_REUSED", "This idempotency key was already used")
        return existing

    booking = db.get(Booking, booking_id)
    if booking is None or booking.requester_id != user.id:
        raise not_found("Booking")
    if booking.status not in PAYABLE_STATUSES:
        raise conflict("BOOKING_NOT_PAYABLE", f"A {booking.status} booking cannot be paid")

    gateway = get_payment_gateway()
    payment = Payment(
        booking_id=booking.id,
        created_by=user.id,
        provider=gateway.name,
        idempotency_key=key,
        amount_minor=booking.fare_minor,
        currency=booking.currency,
        is_simulated=gateway.is_simulated,
    )
    db.add(payment)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise conflict("PAYMENT_EXISTS", "This booking already has a live payment") from exc

    try:
        payment.provider_reference = gateway.create_charge(payment)
    except Exception as exc:
        payment.status = PaymentStatus.FAILED
        db.commit()
        raise AppError(502, "PAYMENT_PROVIDER_ERROR", "The payment could not start") from exc
    audit.record(db, user.id, "payment.started", "payment", payment.id)
    db.commit()
    return payment


def apply_provider_event(db: Session, event: ProviderEvent) -> Payment:
    """Safe to run twice: a repeated callback changes nothing."""
    payment = db.scalar(
        select(Payment)
        .where(Payment.provider_reference == event.provider_reference)
        .with_for_update()
    )
    if payment is None:
        raise not_found("Payment")
    if (event.amount_minor, event.currency) != (payment.amount_minor, payment.currency):
        audit.record(
            db,
            None,
            "payment.amount_mismatch",
            "payment",
            payment.id,
            received_minor=event.amount_minor,
            currency=event.currency,
        )
        db.commit()
        raise AppError(422, "AMOUNT_MISMATCH", "Amount does not match the payment")
    if payment.status != PaymentStatus.PENDING:
        return payment

    booking = db.get(Booking, payment.booking_id)
    if event.status == PaymentStatus.FAILED:
        payment.status = PaymentStatus.FAILED
    else:
        payment.status = PaymentStatus.SUCCEEDED
        _post_collection(db, payment)
        allocate_if_ready(db, booking)
        if booking.status in UNFULFILLED_STATUSES:  # money arrived after cancellation
            refund_booking(db, booking, actor_id=None)
    audit.record(db, None, f"payment.{payment.status.lower()}", "payment", payment.id)
    db.commit()
    return payment


def _post_collection(db: Session, payment: Payment) -> None:
    ledger.post(
        db,
        f"payment:{payment.id}:collected",
        LedgerEvent.PAYMENT_COLLECTED,
        [
            Line(
                ledger.account(db, AccountType.PAYMENT_CLEARING),
                LedgerSide.DEBIT,
                payment.amount_minor,
            ),
            Line(
                ledger.account(db, AccountType.BOOKING_LIABILITY),
                LedgerSide.CREDIT,
                payment.amount_minor,
            ),
        ],
        booking_id=payment.booking_id,
        payment_id=payment.id,
    )


def _succeeded_payment(db: Session, booking_id: uuid.UUID) -> Payment | None:
    return db.scalar(
        select(Payment).where(
            Payment.booking_id == booking_id, Payment.status == PaymentStatus.SUCCEEDED
        )
    )


def allocate_if_ready(db: Session, booking: Booking) -> None:
    """Splits the fare once the money is in and the delivery is done, in either order."""
    payment = _succeeded_payment(db, booking.id)
    if payment is None or booking.status != BookingStatus.DELIVERED:
        return
    rider_share = payment.amount_minor - booking.platform_fee_minor
    lines = [
        Line(
            ledger.account(db, AccountType.BOOKING_LIABILITY),
            LedgerSide.DEBIT,
            payment.amount_minor,
        ),
        Line(
            ledger.account(db, AccountType.RIDER_PAYABLE, booking.rider_id),
            LedgerSide.CREDIT,
            rider_share,
        ),
    ]
    if booking.platform_fee_minor:
        lines.append(
            Line(
                ledger.account(db, AccountType.PLATFORM_FEE_PAYABLE),
                LedgerSide.CREDIT,
                booking.platform_fee_minor,
            )
        )
    ledger.post(
        db,
        f"booking:{booking.id}:allocation",
        LedgerEvent.DELIVERY_ALLOCATED,
        lines,
        booking_id=booking.id,
        payment_id=payment.id,
    )


def refund_booking(db: Session, booking: Booking, actor_id: uuid.UUID | None) -> None:
    """Posts a new reversing transaction. The original entries stay untouched."""
    payment = _succeeded_payment(db, booking.id)
    if payment is None:
        return
    get_payment_gateway().refund(payment)
    ledger.post(
        db,
        f"payment:{payment.id}:refund",
        LedgerEvent.REFUND,
        [
            Line(
                ledger.account(db, AccountType.BOOKING_LIABILITY),
                LedgerSide.DEBIT,
                payment.amount_minor,
            ),
            Line(
                ledger.account(db, AccountType.PAYMENT_CLEARING),
                LedgerSide.CREDIT,
                payment.amount_minor,
            ),
        ],
        booking_id=booking.id,
        payment_id=payment.id,
    )
    payment.status = PaymentStatus.REFUNDED
    audit.record(db, actor_id, "payment.refunded", "payment", payment.id)


def rider_balance_minor(db: Session, rider_id: uuid.UUID) -> int:
    return ledger.credit_balance(db, ledger.account(db, AccountType.RIDER_PAYABLE, rider_id))


def pay_out_rider(
    db: Session, actor: User, rider_id: uuid.UUID, amount_minor: int, key: str
) -> dict:
    """Simulated settlement: there is no live payout provider yet."""
    payable = ledger.account(db, AccountType.RIDER_PAYABLE, rider_id)
    idempotency_key = f"payout:{key}"
    already_posted = ledger.find(db, idempotency_key)
    if already_posted is None and amount_minor > ledger.credit_balance(db, payable):
        raise conflict("INSUFFICIENT_BALANCE", "The rider's balance is lower than this payout")
    transaction = ledger.post(
        db,
        idempotency_key,
        LedgerEvent.RIDER_PAYOUT,
        [
            Line(payable, LedgerSide.DEBIT, amount_minor),
            Line(ledger.account(db, AccountType.PAYOUT_CLEARING), LedgerSide.CREDIT, amount_minor),
        ],
    )
    if already_posted is None:
        audit.record(db, actor.id, "payout.posted", "ledger_transaction", transaction.id)
    db.commit()
    return {
        "transaction_id": transaction.id,
        "balance_minor": ledger.credit_balance(db, payable),
        "is_simulated": True,
    }


def can_simulate(user: User, payment: Payment) -> None:
    if not get_payment_gateway().is_simulated or not payment.is_simulated:
        raise forbidden("This payment is not a simulation")
    if payment.created_by != user.id:
        raise not_found("Payment")
