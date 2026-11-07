import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import SessionLocal, create_tables
from app.errors import AppError, app_error_handler
from app.routers import admin, auth, bookings, media, meta, misc, riders
from app.seed import seed

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    create_tables()
    if settings.seed_on_startup:
        with SessionLocal() as db:
            seed(db)
    if settings.sms_provider == "demo":
        logger.warning("SMS demo inbox is on: anyone can read codes at /api/demo/inbox")
    yield


app = FastAPI(
    title="Nelp API",
    version="0.1.0",
    summary="Choose a delivery rider by reputation, availability and service area.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=False,  # auth is a bearer token, not a cookie
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_exception_handler(AppError, app_error_handler)

for module in (meta, auth, media, riders, bookings, misc, admin):
    app.include_router(module.router)
