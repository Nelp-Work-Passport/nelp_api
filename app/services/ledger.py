"""Balanced, append-only ledger. Every money event goes through post()."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    AccountType,
    LedgerAccount,
    LedgerEntry,
    LedgerEvent,
    LedgerSide,
    LedgerTransaction,
)

PLATFORM = "platform"


@dataclass(frozen=True)
class Line:
    account: LedgerAccount
    side: LedgerSide
    amount_minor: int


def account(
    db: Session, account_type: AccountType, rider_id: uuid.UUID | None = None, currency="KES"
) -> LedgerAccount:
    owner_type = "rider" if rider_id else PLATFORM
    owner_id = str(rider_id) if rider_id else PLATFORM
    found = _find_account(db, owner_type, owner_id, account_type, currency)
    if found:
        return found
    try:
        with db.begin_nested():
            created = LedgerAccount(
                owner_type=owner_type,
                owner_id=owner_id,
                account_type=account_type,
                currency=currency,
            )
            db.add(created)
        return created
    except IntegrityError:  # another request created it first
        return _find_account(db, owner_type, owner_id, account_type, currency)


def _find_account(db, owner_type, owner_id, account_type, currency) -> LedgerAccount | None:
    return db.scalar(
        select(LedgerAccount).where(
            LedgerAccount.owner_type == owner_type,
            LedgerAccount.owner_id == owner_id,
            LedgerAccount.account_type == account_type,
            LedgerAccount.currency == currency,
        )
    )


def find(db: Session, idempotency_key: str) -> LedgerTransaction | None:
    return db.scalar(
        select(LedgerTransaction).where(LedgerTransaction.idempotency_key == idempotency_key)
    )


def post(
    db: Session,
    idempotency_key: str,
    event_type: LedgerEvent,
    lines: Sequence[Line],
    booking_id: uuid.UUID | None = None,
    payment_id: uuid.UUID | None = None,
) -> LedgerTransaction:
    """Returns the existing transaction when this key was already posted."""
    existing = find(db, idempotency_key)
    if existing:
        return existing

    if len(lines) < 2:
        raise ValueError("a ledger transaction needs at least two entries")
    if any(line.amount_minor <= 0 for line in lines):
        raise ValueError("ledger amounts must be positive")
    if len({line.account.currency for line in lines}) != 1:
        raise ValueError("a ledger transaction must use one currency")
    debits = sum(x.amount_minor for x in lines if x.side == LedgerSide.DEBIT)
    credits = sum(x.amount_minor for x in lines if x.side == LedgerSide.CREDIT)
    if debits != credits:
        raise ValueError(f"unbalanced transaction: debits {debits}, credits {credits}")

    transaction = LedgerTransaction(
        idempotency_key=idempotency_key,
        event_type=event_type,
        booking_id=booking_id,
        payment_id=payment_id,
        entries=[
            LedgerEntry(account=x.account, side=x.side, amount_minor=x.amount_minor) for x in lines
        ],
    )
    db.add(transaction)
    db.flush()
    return transaction


def credit_balance(db: Session, account_row: LedgerAccount) -> int:
    """Credits minus debits, which is the amount owed on a payable account."""
    signed = case(
        (LedgerEntry.side == LedgerSide.CREDIT, LedgerEntry.amount_minor),
        else_=-LedgerEntry.amount_minor,
    )
    total = db.scalar(
        select(func.coalesce(func.sum(signed), 0)).where(LedgerEntry.account_id == account_row.id)
    )
    return int(total)


def booking_transactions(db: Session, booking_id: uuid.UUID) -> list[LedgerTransaction]:
    return list(
        db.scalars(
            select(LedgerTransaction)
            .where(LedgerTransaction.booking_id == booking_id)
            .order_by(LedgerTransaction.created_at)
        )
    )


def trial_balance(db: Session) -> list[dict]:
    rows = db.execute(
        select(
            LedgerAccount,
            func.coalesce(
                func.sum(
                    case((LedgerEntry.side == LedgerSide.DEBIT, LedgerEntry.amount_minor), else_=0)
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case((LedgerEntry.side == LedgerSide.CREDIT, LedgerEntry.amount_minor), else_=0)
                ),
                0,
            ),
        )
        .join(LedgerEntry, LedgerEntry.account_id == LedgerAccount.id)
        .group_by(LedgerAccount.id)
    ).all()
    return [
        {
            "account_type": row[0].account_type,
            "owner_type": row[0].owner_type,
            "owner_id": row[0].owner_id,
            "currency": row[0].currency,
            "debits_minor": int(row[1]),
            "credits_minor": int(row[2]),
        }
        for row in rows
    ]
