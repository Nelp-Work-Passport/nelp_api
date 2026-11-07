from datetime import timedelta

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import presenters, schemas
from app.config import get_settings
from app.db import utcnow
from app.deps import CurrentUser, Db
from app.errors import AppError, conflict
from app.models import (
    PhoneConfirmation,
    RiderProfile,
    SmsType,
    User,
    UserRole,
    VerificationStatus,
)
from app.rate_limit import limiter
from app.security import code_matches, create_token, generate_code, hash_code, hash_pin, verify_pin
from app.services import notifications

router = APIRouter(prefix="/api/auth", tags=["auth"])
CODE_PURPOSE = "phone"


def _send_phone_code(db, user: User, confirmation: PhoneConfirmation):
    code = generate_code()
    confirmation.code_hash = hash_code(CODE_PURPOSE, user.id, code)
    confirmation.expires_at = utcnow() + timedelta(minutes=15)
    confirmation.attempts = 0
    db.commit()
    message = notifications.send(
        db,
        user.phone,
        f"Nelp: Your confirmation code is {code}. It expires in 15 minutes.",
        SmsType.PHONE_CONFIRMATION,
        user_id=user.id,
    )
    confirmation.sent_at = message.sent_at
    db.commit()
    return message


@router.post("/register", response_model=schemas.AuthOut, status_code=201)
def register(body: schemas.RegisterIn, db: Db):
    limiter.hit(f"register:{body.phone}", 5, 3600)
    if db.scalar(select(User.id).where(User.phone == body.phone)):
        raise conflict("PHONE_TAKEN", "This phone number is already registered")

    user = User(
        phone=body.phone,
        pin_hash=hash_pin(body.pin),
        display_name=body.display_name,
        role_rows=[UserRole(role=role) for role in set(body.roles)],
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise conflict("PHONE_TAKEN", "This phone number is already registered") from exc
    confirmation = PhoneConfirmation(user_id=user.id, code_hash="", expires_at=utcnow())
    db.add(confirmation)
    message = _send_phone_code(db, user, confirmation)
    return schemas.AuthOut(
        access_token=create_token(user.id),
        user=presenters.user_out(db, user),
        sms_status=message.status,
    )


@router.post("/login", response_model=schemas.AuthOut)
def login(body: schemas.LoginIn, db: Db):
    limiter.hit(f"login:{body.phone}", 8, 900)
    user = db.scalar(select(User).where(User.phone == body.phone))
    if not verify_pin(user.pin_hash if user else None, body.pin) or not user.is_active:
        raise AppError(401, "BAD_CREDENTIALS", "Phone number or PIN is wrong")
    limiter.clear(f"login:{body.phone}")
    return schemas.AuthOut(access_token=create_token(user.id), user=presenters.user_out(db, user))


@router.get("/me", response_model=schemas.UserOut)
def me(user: CurrentUser, db: Db):
    return presenters.user_out(db, user)


@router.post("/confirm-phone", response_model=schemas.UserOut)
def confirm_phone(body: schemas.ConfirmPhoneIn, user: CurrentUser, db: Db):
    if user.phone_confirmed_at:
        return presenters.user_out(db, user)
    settings = get_settings()
    confirmation = db.scalar(
        select(PhoneConfirmation).where(PhoneConfirmation.user_id == user.id).with_for_update()
    )
    if confirmation is None:
        raise conflict("NO_CODE", "Request a new code")
    if confirmation.attempts >= settings.max_code_attempts:
        raise AppError(429, "CODE_LOCKED", "Too many wrong codes. Request a new one.")
    if confirmation.expires_at <= utcnow():
        raise AppError(410, "CODE_EXPIRED", "This code has expired. Request a new one.")
    if not code_matches(confirmation.code_hash, CODE_PURPOSE, user.id, body.code):
        confirmation.attempts += 1
        db.commit()
        raise AppError(400, "WRONG_CODE", "Wrong code")

    now = utcnow()
    confirmation.confirmed_at = now
    user.phone_confirmed_at = now
    rider = db.get(RiderProfile, user.id)
    if rider and rider.verification_status == VerificationStatus.UNVERIFIED:
        rider.verification_status = VerificationStatus.PHONE_CONFIRMED
    db.commit()
    return presenters.user_out(db, user)


@router.post("/resend-code", response_model=schemas.SmsOut)
def resend_code(user: CurrentUser, db: Db):
    if user.phone_confirmed_at:
        raise conflict("ALREADY_CONFIRMED", "This phone is already confirmed")
    limiter.hit(f"phone-code:{user.id}", 3, 600)
    confirmation = db.scalar(select(PhoneConfirmation).where(PhoneConfirmation.user_id == user.id))
    if confirmation.resend_count >= get_settings().max_code_resends:
        raise AppError(429, "RESEND_LIMIT", "This code cannot be resent again. Contact support.")
    confirmation.resend_count += 1
    message = _send_phone_code(db, user, confirmation)
    return schemas.SmsOut(sms_status=message.status)
