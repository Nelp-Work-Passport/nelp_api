import json

import pytest

from app.adapters.payments import SimulatedGateway
from app.models import (
    AccountType,
    LedgerEntry,
    LedgerEvent,
    LedgerSide,
    LedgerTransaction,
)
from app.services import ledger
from app.services.ledger import Line


def webhook(client, payment, status="SUCCEEDED", amount=None, signature=None):
    body = json.dumps(
        {
            "provider_reference": payment["provider_reference"],
            "status": status,
            "amount_minor": amount or payment["amount_minor"],
            "currency": "KES",
        }
    ).encode()
    return client.post(
        "/api/payments/webhook",
        content=body,
        headers={"X-Nelp-Signature": signature or SimulatedGateway.sign(body)},
    )


def intent(world, customer, booking_id, key="key-12345678"):
    return world.client.post(
        "/api/payments/intent",
        json={"booking_id": booking_id},
        headers={**customer["headers"], "Idempotency-Key": key},
    )


def test_delivered_booking_splits_fare_and_balances(world, client):
    """DoD 5."""
    rider = world.rider()
    customer = world.customer()
    booking_id = world.deliver(customer, rider)
    payment = world.pay(customer, booking_id)
    assert payment["status"] == "SUCCEEDED"
    assert payment["is_simulated"] is True

    result = world.ledger(booking_id, customer)
    assert result["balanced"] is True
    assert result["total_debits_minor"] == result["total_credits_minor"] > 0
    allocation = result["transactions"][1]["entries"]
    by_type = {entry["account_type"]: entry for entry in allocation}
    booking = client.get(f"/api/bookings/{booking_id}", headers=customer["headers"]).json()
    assert by_type["RIDER_PAYABLE"]["amount_minor"] == (
        booking["fare_minor"] - booking["platform_fee_minor"]
    )
    assert by_type["PLATFORM_FEE_PAYABLE"]["amount_minor"] == booking["platform_fee_minor"]

    me = client.get("/api/riders/me", headers=rider["headers"]).json()
    assert me["balance_minor"] == booking["fare_minor"] - booking["platform_fee_minor"]


def test_duplicate_callback_does_not_double_post(world, client):
    customer = world.customer()
    booking_id = world.booking(customer, world.rider())
    payment = intent(world, customer, booking_id).json()
    assert webhook(client, payment).status_code == 200
    assert webhook(client, payment).status_code == 200
    assert webhook(client, payment).status_code == 200
    assert len(world.ledger(booking_id, customer)["transactions"]) == 1


def test_failure_after_success_is_ignored(world, client):
    customer = world.customer()
    booking_id = world.booking(customer, world.rider())
    payment = intent(world, customer, booking_id).json()
    webhook(client, payment)
    assert webhook(client, payment, "FAILED").json()["status"] == "SUCCEEDED"


def test_retry_with_same_key_returns_same_payment(world):
    customer = world.customer()
    booking_id = world.booking(customer, world.rider())
    first = intent(world, customer, booking_id).json()
    second = intent(world, customer, booking_id).json()
    assert first["id"] == second["id"]


def test_second_live_payment_is_refused(world):
    customer = world.customer()
    booking_id = world.booking(customer, world.rider())
    intent(world, customer, booking_id)
    response = intent(world, customer, booking_id, key="different-key-1")
    assert response.status_code == 409
    assert response.json()["code"] == "PAYMENT_EXISTS"


def test_failed_payment_can_be_retried(world):
    customer = world.customer()
    booking_id = world.booking(customer, world.rider())
    assert world.pay(customer, booking_id, "key-first-1", "FAILED")["status"] == "FAILED"
    assert world.ledger(booking_id, customer)["transactions"] == []
    assert world.pay(customer, booking_id, "key-second-2")["status"] == "SUCCEEDED"


def test_key_cannot_be_reused_across_bookings(world):
    customer = world.customer()
    rider = world.rider()
    first = world.booking(customer, rider)
    second = world.booking(customer, rider)
    intent(world, customer, first)
    response = intent(world, customer, second)
    assert response.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_stranger_cannot_pay_or_simulate(world, client):
    customer = world.customer()
    stranger = world.customer()
    booking_id = world.booking(customer, world.rider())
    assert intent(world, stranger, booking_id).status_code == 404
    payment = intent(world, customer, booking_id).json()
    response = client.post(
        f"/api/payments/{payment['id']}/simulate",
        json={"outcome": "SUCCEEDED"},
        headers=stranger["headers"],
    )
    assert response.status_code == 404


def test_webhook_rejects_bad_signature_and_wrong_amount(world, client):
    customer = world.customer()
    booking_id = world.booking(customer, world.rider())
    payment = intent(world, customer, booking_id).json()
    assert webhook(client, payment, signature="forged").status_code == 401
    assert webhook(client, payment, amount=1).status_code == 422
    assert world.ledger(booking_id, customer)["transactions"] == []


@pytest.mark.parametrize("how", ["cancel", "decline"])
def test_refund_reverses_with_a_new_transaction(world, client, how):
    rider = world.rider()
    customer = world.customer()
    booking_id = world.booking(customer, rider)
    world.pay(customer, booking_id)
    if how == "cancel":
        world.act(booking_id, "cancel", customer, {})
    else:
        world.act(booking_id, "decline", rider, {})
    result = world.ledger(booking_id, customer)
    assert [t["event_type"] for t in result["transactions"]] == [
        "PAYMENT_COLLECTED",
        "REFUND",
    ]
    assert result["balanced"] is True
    booking = client.get(f"/api/bookings/{booking_id}", headers=customer["headers"]).json()
    assert booking["payment"]["status"] == "REFUNDED"


def test_money_arriving_after_cancellation_is_refunded(world, client):
    customer = world.customer()
    booking_id = world.booking(customer, world.rider())
    payment = intent(world, customer, booking_id).json()
    world.act(booking_id, "cancel", customer, {})
    response = webhook(client, payment)
    assert response.json()["status"] == "REFUNDED"
    events = [t["event_type"] for t in world.ledger(booking_id, customer)["transactions"]]
    assert events == ["PAYMENT_COLLECTED", "REFUND"]


def test_payout_reduces_balance_and_is_idempotent(world, client):
    rider = world.rider()
    customer = world.customer()
    admin = world.admin()
    booking_id = world.deliver(customer, rider)
    world.pay(customer, booking_id)
    balance = client.get("/api/riders/me", headers=rider["headers"]).json()["balance_minor"]
    headers = {**admin["headers"], "Idempotency-Key": "payout-0001"}
    path = f"/api/admin/riders/{rider['id']}/payouts"

    too_much = client.post(path, json={"amount_minor": balance + 1}, headers=headers)
    assert too_much.json()["code"] == "INSUFFICIENT_BALANCE"

    first = client.post(path, json={"amount_minor": balance}, headers=headers)
    assert first.json()["balance_minor"] == 0
    assert first.json()["is_simulated"] is True
    again = client.post(path, json={"amount_minor": balance}, headers=headers)
    assert again.json()["transaction_id"] == first.json()["transaction_id"]
    assert again.json()["balance_minor"] == 0


def test_trial_balance_holds_across_events(world, client):
    rider = world.rider()
    customer = world.customer()
    admin = world.admin()
    world.pay(customer, world.deliver(customer, rider))
    refunded = world.booking(customer, rider)
    world.pay(customer, refunded)
    world.act(refunded, "cancel", customer, {})
    result = client.get("/api/admin/ledger/trial-balance", headers=admin["headers"]).json()
    assert result["balanced"] is True
    assert result["total_debits_minor"] > 0


def test_ledger_refuses_unbalanced_transaction(db):
    clearing = ledger.account(db, AccountType.PAYMENT_CLEARING)
    liability = ledger.account(db, AccountType.BOOKING_LIABILITY)
    with pytest.raises(ValueError, match="unbalanced"):
        ledger.post(
            db,
            "bad-key",
            LedgerEvent.PAYMENT_COLLECTED,
            [Line(clearing, LedgerSide.DEBIT, 100), Line(liability, LedgerSide.CREDIT, 99)],
        )


def test_ledger_rows_cannot_be_changed_or_deleted(world, db):
    customer = world.customer()
    world.pay(customer, world.booking(customer, world.rider()))
    entry = db.query(LedgerEntry).first()
    entry.amount_minor += 1
    with pytest.raises(ValueError, match="append-only"):
        db.commit()
    db.rollback()
    db.delete(db.query(LedgerTransaction).first())
    with pytest.raises(ValueError, match="append-only"):
        db.commit()
    db.rollback()
