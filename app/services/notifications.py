"""Outgoing SMS. A failed send is recorded and never raises into booking code."""

import logging
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.sms import gateway_for
from app.config import get_settings
from app.db import utcnow
from app.models import Booking, SmsMessage, SmsStatus, SmsType

logger = logging.getLogger(__name__)
NAIROBI = ZoneInfo("Africa/Nairobi")


def nairobi_time(moment: datetime) -> str:
    local = moment.astimezone(NAIROBI)
    if local.date() == utcnow().astimezone(NAIROBI).date():
        return local.strftime("%I:%M %p").lstrip("0")
    return local.strftime("%d %b, %I:%M %p")


def tracking_url(booking: Booking) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/t/{booking.tracking_token}"


def send(
    db: Session,
    phone: str,
    text: str,
    message_type: SmsType,
    booking_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> SmsMessage:
    """Commits the record first, so a provider failure still leaves a trace."""
    gateway = gateway_for(db, phone)
    message = SmsMessage(
        booking_id=booking_id,
        recipient_user_id=user_id,
        recipient_phone=phone,
        message_type=message_type,
        provider=gateway.name,
    )
    db.add(message)
    db.commit()
    try:
        message.provider_reference = gateway.send(phone, text)
        message.status = SmsStatus.SENT
        message.sent_at = utcnow()
    except Exception as exc:
        logger.warning("SMS %s failed: %s", message.id, exc)
        message.status = SmsStatus.FAILED
        message.error = str(exc)[:280]
    db.commit()
    return message


def delivery_code_text(booking: Booking, code: str) -> str:
    return (
        f"Nelp: A delivery with {booking.rider.display_name} is booked for "
        f"{nairobi_time(booking.scheduled_at)}. Confirm details: {tracking_url(booking)}. "
        f"Code: {code}"
    )


def latest_status(db: Session, booking_id: uuid.UUID, message_type: SmsType) -> SmsStatus | None:
    return db.scalar(
        select(SmsMessage.status)
        .where(SmsMessage.booking_id == booking_id, SmsMessage.message_type == message_type)
        .order_by(SmsMessage.created_at.desc())
        .limit(1)
    )
