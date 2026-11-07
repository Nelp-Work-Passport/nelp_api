import uuid

from fastapi import APIRouter
from sqlalchemy import select, text

from app import schemas
from app.adapters import storage
from app.adapters.payments import get_payment_gateway
from app.config import get_settings
from app.deps import Db
from app.errors import AppError, not_found
from app.models import ServiceArea
from app.services import fares

router = APIRouter(tags=["meta"])


@router.get("/health")
def health(db: Db):
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise AppError(503, "DATABASE_DOWN", "The database is not reachable") from exc
    settings = get_settings()
    return {
        "status": "ok",
        "sms_provider": settings.sms_provider,
        "payments_simulated": get_payment_gateway().is_simulated,
        "storage_configured": storage.is_configured(),
    }


@router.get("/api/areas", response_model=list[schemas.AreaOut])
def list_areas(db: Db):
    return db.scalars(select(ServiceArea).order_by(ServiceArea.name)).all()


@router.get("/api/fares/estimate", response_model=schemas.FareOut)
def estimate_fare(pickup_area_id: uuid.UUID, dropoff_area_id: uuid.UUID, db: Db):
    pickup = db.get(ServiceArea, pickup_area_id)
    dropoff = db.get(ServiceArea, dropoff_area_id)
    if pickup is None or dropoff is None:
        raise not_found("Service area")
    quote = fares.quote(pickup, dropoff)
    return schemas.FareOut(
        distance_km=quote.distance_km,
        fare_minor=quote.fare_minor,
        platform_fee_minor=quote.platform_fee_minor,
        rider_earning_minor=quote.rider_earning_minor,
        currency=quote.currency,
    )
