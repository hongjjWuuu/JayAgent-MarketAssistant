from __future__ import annotations

import contextvars
from dataclasses import dataclass
from uuid import uuid4

import httpx
import jwt
from fastapi import HTTPException, Request
from jwt import PyJWKClient, PyJWKClientError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from app.core.config import settings


@dataclass(frozen=True)
class AuthContext:
    tenant_id: str
    subject: str
    role: str
    request_id: str
    username: str
    customer_id: str | None = None


_auth_context: contextvars.ContextVar[AuthContext | None] = contextvars.ContextVar(
    "market_assistant_auth_context", default=None
)


def current_auth() -> AuthContext:
    context = _auth_context.get()
    if context is None:
        raise HTTPException(status_code=401, detail="Authentication context is required")
    return context


class OidcTokenValidator:
    def __init__(self) -> None:
        self._jwks_client: PyJWKClient | None = None

    def _client(self) -> PyJWKClient:
        if self._jwks_client is None:
            url = settings.oidc_jwks_url or f"{settings.oidc_issuer}/protocol/openid-connect/certs"
            self._jwks_client = PyJWKClient(url, cache_keys=True, lifespan=300)
        return self._jwks_client

    def validate(self, token: str) -> dict:
        try:
            key = self._client().get_signing_key_from_jwt(token)
        except PyJWKClientError:
            # A single fresh fetch supports Keycloak signing-key rotation without
            # creating an authentication downgrade path.
            self._jwks_client = None
            key = self._client().get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            audience=settings.oidc_audience,
            issuer=settings.oidc_issuer,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )


oidc_validator = OidcTokenValidator()


class AuthenticationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id") or f"req_{uuid4().hex}"
        request.state.request_id = request_id
        if not request.url.path.startswith("/api/v1/"):
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        try:
            auth = self._authenticate(request, request_id)
        except HTTPException as error:
            return JSONResponse(
                status_code=error.status_code,
                content={
                    "error_code": "AUTHENTICATION_FAILED"
                    if error.status_code == 401
                    else "FORBIDDEN",
                    "message": str(error.detail),
                    "request_id": request_id,
                    "details": {},
                },
                headers={"X-Request-ID": request_id},
            )
        token = _auth_context.set(auth)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            _auth_context.reset(token)

    def _authenticate(self, request: Request, request_id: str) -> AuthContext:
        if settings.auth_mode == "development_headers":
            if not settings.allow_dev_auth:
                raise HTTPException(
                    status_code=401, detail="Development authentication is disabled"
                )
            tenant_id = request.headers.get("x-tenant-id", "").strip()
            subject = request.headers.get("x-user-id", "").strip()
            requested_role = request.headers.get("x-role", "customer").strip().lower()
            username = subject
        else:
            header = request.headers.get("authorization", "")
            if not header.startswith("Bearer "):
                raise HTTPException(status_code=401, detail="Bearer token is required")
            try:
                claims = oidc_validator.validate(header.removeprefix("Bearer ").strip())
            except (jwt.PyJWTError, httpx.HTTPError, ValueError) as error:
                raise HTTPException(
                    status_code=401, detail="Invalid or expired bearer token"
                ) from error
            subject = str(claims["sub"])
            username = str(claims.get("preferred_username") or subject)
            tenant_id = str(claims.get("tenant_id") or "")
            requested_role = ""

        if not tenant_id or not subject:
            raise HTTPException(status_code=401, detail="Tenant and subject are required")
        from app.repositories.factory import get_repository

        stored_role = get_repository().member_role(tenant_id, subject)
        if stored_role is None:
            raise HTTPException(status_code=403, detail="Subject is not an active tenant member")
        if settings.auth_mode == "development_headers" and requested_role != stored_role:
            raise HTTPException(
                status_code=403, detail="Requested role does not match tenant membership"
            )
        customer_id = None
        if stored_role == "customer":
            if settings.auth_mode == "development_headers":
                customer_id = subject
            else:
                customer_id = get_repository().resolve_channel_customer(
                    tenant_id, "oidc", subject
                )
                if customer_id is None:
                    raise HTTPException(
                        status_code=403, detail="Customer identity is not linked"
                    )
        return AuthContext(
            tenant_id=tenant_id,
            subject=subject,
            role=stored_role,
            request_id=request_id,
            username=username,
            customer_id=customer_id,
        )


def require_roles(auth: AuthContext, *roles: str) -> None:
    if auth.role not in roles:
        raise HTTPException(status_code=403, detail="Role is not allowed for this operation")
