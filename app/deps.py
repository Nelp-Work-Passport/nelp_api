"""Shared request dependencies."""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import AppError, forbidden
from app.models import Role, User
from app.security import decode_token

_bearer = HTTPBearer(auto_error=False)
Db = Annotated[Session, Depends(get_db)]


def current_user(
    db: Db,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    user_id = decode_token(credentials.credentials) if credentials else None
    user = db.get(User, user_id) if user_id else None
    if user is None or not user.is_active:
        raise AppError(401, "UNAUTHENTICATED", "Log in to continue", {"WWW-Authenticate": "Bearer"})
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_role(*roles: Role):
    def checker(user: CurrentUser) -> User:
        if not user.roles.intersection(roles):
            raise forbidden(f"This needs one of these roles: {', '.join(roles)}")
        return user

    return Depends(checker)


Rider = Annotated[User, require_role(Role.RIDER)]
Requester = Annotated[User, require_role(Role.CUSTOMER, Role.MERCHANT)]
Admin = Annotated[User, require_role(Role.ADMIN)]
