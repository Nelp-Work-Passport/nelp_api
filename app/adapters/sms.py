"""SMS gateways. Booking code only ever sees SmsGateway.send()."""

import uuid
from typing import Protocol

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import DemoInboxMessage


class SmsSendError(Exception):
    pass


class SmsGateway(Protocol):
    name: str

    def send(self, phone: str, text: str) -> str | None:
        """Send one message and return the provider's reference, if it gives one."""


class DemoInboxSms:
    """Stores the message for the demo inbox instead of sending it."""

    name = "demo_inbox"

    def __init__(self, db: Session):
        self.db = db

    def send(self, phone: str, text: str) -> str:
        self.db.add(DemoInboxMessage(phone=phone, body=text))
        return f"demo-{uuid.uuid4()}"


class ThriveVasSms:
    """Not implemented yet, on purpose.

    ThriveVas publishes no public API reference, and the proposal says not to
    guess the request schema. At hour 0, confirm the endpoint, auth, phone
    format and sender ID with ThriveVas, then replace the body of send().
    Until then every send fails visibly and the booking is kept, so the demo
    inbox (SMS_PROVIDER=demo) is the working path.
    """

    name = "thrivevas"

    def send(self, phone: str, text: str) -> str:
        raise SmsSendError("ThriveVas adapter is not configured yet")


def gateway_for(db: Session, phone: str) -> SmsGateway:
    settings = get_settings()
    if settings.sms_provider == "demo" or phone.startswith(settings.demo_phone_prefix):
        return DemoInboxSms(db)
    return ThriveVasSms()
