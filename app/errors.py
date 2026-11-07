"""One error shape for the front end: {"detail": "...", "code": "..."}."""

from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers
        super().__init__(message)


def not_found(what: str = "Resource") -> AppError:
    return AppError(404, "NOT_FOUND", f"{what} not found")


def conflict(code: str, message: str) -> AppError:
    return AppError(409, code, message)


def forbidden(message: str = "You are not allowed to do that") -> AppError:
    return AppError(403, "FORBIDDEN", message)


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={"detail": exc.message, "code": exc.code},
        headers=exc.headers,
    )
