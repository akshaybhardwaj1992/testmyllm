from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import COOKIE, current_user
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import ApiError
from app.core.security import create_token, verify_password
from app.models import User
from app.schemas import LoginIn, LoginOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginOut)
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)) -> LoginOut:
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise ApiError(401, "invalid_credentials", "Email or password is incorrect")
    token = create_token(user.id)
    s = get_settings()
    response.set_cookie(
        COOKIE, token, httponly=True, samesite="strict", secure=s.cookie_secure,
        max_age=s.jwt_ttl_minutes * 60, path="/",
    )
    return LoginOut(token=token, user=UserOut.model_validate(user))


@router.post("/logout", status_code=204)
def logout(response: Response) -> Response:
    response.delete_cookie(COOKIE, path="/")
    response.status_code = 204
    return response


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> User:
    return user
