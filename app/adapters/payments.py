"""Payment gateways. Everything provider-specific stays behind this interface."""

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from typing import Protocol

from app.config import get_settings
from app.errors import AppError
from app.models import Payment, PaymentStatus


@dataclass(frozen=True)
class ProviderEvent:
    provider_reference: str
    status: PaymentStatus  # SUCCEEDED or FAILED
    amount_minor: int
    currency: str


class PaymentGateway(Protocol):
    name: str
    is_simulated: bool

    def create_charge(self, payment: Payment) -> str:
        """Start a charge and return the provider's reference."""

    def refund(self, payment: Payment) -> None:
        """Return the money to the customer."""

    def parse_webhook(self, body: bytes, signature: str | None) -> ProviderEvent:
        """Verify a callback and translate it into a ProviderEvent."""


class SimulatedGateway:
    """Moves no money. Responses carry is_simulated so the UI can label it."""

    name = "simulated"
    is_simulated = True

    def create_charge(self, payment: Payment) -> str:
        return f"sim-{uuid.uuid4()}"

    def refund(self, payment: Payment) -> None:
        return None

    @staticmethod
    def sign(body: bytes) -> str:
        secret = get_settings().payment_webhook_secret.encode()
        return hmac.new(secret, body, hashlib.sha256).hexdigest()

    def parse_webhook(self, body: bytes, signature: str | None) -> ProviderEvent:
        if not signature or not hmac.compare_digest(signature, self.sign(body)):
            raise AppError(401, "BAD_SIGNATURE", "Webhook signature is invalid")
        try:
            data = json.loads(body)
            status = PaymentStatus(data["status"])
            if status not in (PaymentStatus.SUCCEEDED, PaymentStatus.FAILED):
                raise ValueError(status)
            return ProviderEvent(
                provider_reference=str(data["provider_reference"]),
                status=status,
                amount_minor=int(data["amount_minor"]),
                currency=str(data["currency"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AppError(400, "BAD_WEBHOOK", "Webhook payload is invalid") from exc


def get_payment_gateway() -> PaymentGateway:
    return SimulatedGateway()
