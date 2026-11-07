"""Approximate distance and fare from coarse neighbourhood centres."""

import math
from dataclasses import dataclass

from app.config import get_settings
from app.models import ServiceArea


@dataclass(frozen=True)
class FareQuote:
    distance_km: float
    fare_minor: int
    platform_fee_minor: int
    rider_earning_minor: int
    currency: str = "KES"


def distance_km(a: ServiceArea, b: ServiceArea) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a.latitude, a.longitude, b.latitude, b.longitude))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return round(6371 * 2 * math.asin(math.sqrt(h)), 1)


def quote(pickup: ServiceArea, dropoff: ServiceArea) -> FareQuote:
    settings = get_settings()
    km = distance_km(pickup, dropoff)
    raw_kes = settings.fare_base_kes + settings.fare_per_km_kes * math.ceil(km)
    fare_kes = math.ceil(raw_kes / 10) * 10  # keep fares round
    fare_minor = fare_kes * 100
    fee_minor = fare_minor * settings.platform_fee_bps // 10_000
    return FareQuote(km, fare_minor, fee_minor, fare_minor - fee_minor)
