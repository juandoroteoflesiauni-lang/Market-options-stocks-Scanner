from __future__ import annotations
from typing import Any
"""Lightweight operator auth for the QuantumAnalyzer web shell."""


import base64
import hashlib
import hmac
import json
import time
import os

from fastapi import APIRouter, Cookie, HTTPException, Response
from pydantic import BaseModel, Field, field_validator

from backend.config.settings import load_settings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

SESSION_COOKIE = "qa_session"
SESSION_TTL_SECONDS = 12 * 60 * 60


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=512)


class ProfilePatchRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=254)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        trimmed = value.strip()
        if "@" not in trimmed or trimmed.startswith("@") or trimmed.endswith("@"):
            raise ValueError("email must be a valid address")
        return trimmed


class OperatorProfile(BaseModel):
    username: str
    display_name: str
    role: str = "operator"
    email: str | None = None
    expires_at: int


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _secret() -> bytes:
    settings = load_settings()
    secret = (settings.qa_session_secret or "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="QA_SESSION_SECRET is not configured")
    return secret.encode("utf-8")


def _verify_password(password: str) -> bool:
    settings = load_settings()
    expected = (settings.qa_app_password_hash or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="QA_APP_PASSWORD_HASH is not configured")

    if expected.startswith("sha256:") or expected.startswith("sha256$"):
        expected_hex = expected.split(":", 1)[-1].split("$", 1)[-1].lower()
        actual_hex = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return hmac.compare_digest(actual_hex, expected_hex)

    parts = expected.split("$")
    if len(parts) == 4 and parts[0] == "pbkdf2_sha256":
        iterations = int(parts[1])
        salt = _b64url_decode(parts[2])
        expected_digest = _b64url_decode(parts[3])
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected_digest)

    raise HTTPException(
        status_code=503,
        detail="Unsupported QA_APP_PASSWORD_HASH format. Use sha256:<hex> or pbkdf2_sha256$iterations$salt$digest.",
    )


def _sign(payload: dict[str, Any]) -> str:
    body = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    sig = hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64url_encode(sig)}"


def _read_session(token: str | None) -> OperatorProfile:
    if not token or "." not in token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    body, sig = token.rsplit(".", 1)
    expected = hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest()
    try:
        actual = _b64url_decode(sig)
        payload = json.loads(_b64url_decode(body))
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=401, detail="Invalid session") from None
    if not hmac.compare_digest(actual, expected):
        raise HTTPException(status_code=401, detail="Invalid session")
    if int(payload.get("exp", 0)) <= int(time.time()):
        raise HTTPException(status_code=401, detail="Session expired")
    return OperatorProfile(
        username=str(payload.get("sub") or ""),
        display_name=str(payload.get("display_name") or payload.get("sub") or "Operator"),
        role=str(payload.get("role") or "operator"),
        email=payload.get("email") or None,
        expires_at=int(payload["exp"]),
    )


def get_current_user(qa_session: str | None = Cookie(default=None)) -> OperatorProfile:
    """FastAPI dependency: validates the HMAC-signed session cookie.

    Raises 401 if the cookie is missing, invalid, or expired.
    """
    return _read_session(qa_session)


def _set_session_cookie(response: Response, profile: OperatorProfile) -> None:
    token = _sign(
        {
            "sub": profile.username,
            "display_name": profile.display_name,
            "role": profile.role,
            "email": profile.email,
            "iat": int(time.time()),
            "exp": profile.expires_at,
        }
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=os.getenv("ENVIRONMENT", "development") != "development",
        samesite="lax",
        path="/",
    )


@router.post("/login")
def login(payload: LoginRequest, response: Response) -> dict[str, OperatorProfile]:
    settings = load_settings()
    username = payload.username.strip()
    if not hmac.compare_digest(username, settings.qa_app_username):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not _verify_password(payload.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    profile = OperatorProfile(
        username=settings.qa_app_username,
        display_name=settings.qa_app_display_name,
        email=settings.qa_app_email,
        expires_at=int(time.time()) + SESSION_TTL_SECONDS,
    )
    _set_session_cookie(response, profile)
    return {"user": profile}


@router.post("/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(qa_session: str | None = Cookie(default=None)) -> dict[str, OperatorProfile]:
    return {"user": _read_session(qa_session)}


@router.patch("/profile")
def patch_profile(
    payload: ProfilePatchRequest,
    response: Response,
    qa_session: str | None = Cookie(default=None),
) -> dict[str, OperatorProfile]:
    current = _read_session(qa_session)
    updated = OperatorProfile(
        username=current.username,
        display_name=payload.display_name.strip() if payload.display_name else current.display_name,
        role=current.role,
        email=payload.email if payload.email is not None else current.email,
        expires_at=current.expires_at,
    )
    _set_session_cookie(response, updated)
    return {"user": updated}
