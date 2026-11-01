from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from app.api.routes import router
from app.core.config import settings, validate_settings
from app.core.errors import ApiError
from app.core.operations import RateLimitMiddleware
from app.core.security import AuthenticationMiddleware
from app.domain.transitions import InvalidStateTransition
from app.observability.metrics import refresh_repository_metrics
from app.persistence.database import database_ready

validate_settings(settings)
logger = logging.getLogger(__name__)

REQUESTS = Counter(
    "market_assistant_http_requests_total", "HTTP requests", ["method", "path", "status"]
)
LATENCY = Histogram("market_assistant_http_request_seconds", "HTTP request latency", ["path"])

app = FastAPI(title=settings.app_name, version="0.6.0-rc1")
app.add_middleware(AuthenticationMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[item.strip() for item in settings.cors_origins.split(",") if item.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    path = request.url.path
    with LATENCY.labels(path=path).time():
        response = await call_next(request)
    REQUESTS.labels(method=request.method, path=path, status=str(response.status_code)).inc()
    return response


@app.exception_handler(HTTPException)
async def http_error(request: Request, error: HTTPException):
    request_id = getattr(request.state, "request_id", "unknown")
    codes = {
        400: "BAD_REQUEST",
        401: "AUTHENTICATION_FAILED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        422: "VALIDATION_ERROR",
    }
    return JSONResponse(
        status_code=error.status_code,
        content={
            "error_code": codes.get(error.status_code, "REQUEST_FAILED"),
            "message": str(error.detail),
            "request_id": request_id,
            "details": {},
        },
    )


@app.exception_handler(ApiError)
async def application_error(request: Request, error: ApiError):
    return JSONResponse(
        status_code=error.status_code,
        content={
            "error_code": error.error_code,
            "message": error.message,
            "request_id": getattr(request.state, "request_id", "unknown"),
            "details": error.details,
        },
    )


@app.exception_handler(InvalidStateTransition)
async def invalid_transition(request: Request, error: InvalidStateTransition):
    try:
        from app.core.security import current_auth
        from app.domain.models import AuditEvent
        from app.repositories.factory import get_repository

        auth = current_auth()
        get_repository().append_audit(
            AuditEvent(
                event_id=f"audit_transition_{getattr(request.state, 'request_id', 'unknown')}",
                tenant_id=auth.tenant_id,
                actor_id=auth.subject,
                action="transition.rejected",
                resource_type=error.entity,
                resource_id=request.url.path,
                request_id=getattr(request.state, "request_id", "unknown"),
                data={"current": error.current.value, "target": error.target.value},
            )
        )
    except Exception as audit_error:  # noqa: BLE001 - response must survive audit outages
        logger.warning("transition rejection audit failed: %s", audit_error)
    return JSONResponse(
        status_code=409,
        content={
            "error_code": "INVALID_STATE_TRANSITION",
            "message": str(error),
            "request_id": getattr(request.state, "request_id", "unknown"),
            "details": {
                "entity": error.entity,
                "current": error.current.value,
                "target": error.target.value,
            },
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error_code": "VALIDATION_ERROR",
            "message": "请求参数不符合接口约束",
            "request_id": getattr(request.state, "request_id", "unknown"),
            "details": {"errors": error.errors()},
        },
    )


app.include_router(router)


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok", "service": "jayagent-market-assistant"}


@app.get("/health/ready")
def ready():
    if settings.persistence_backend == "postgres" and not database_ready():
        return JSONResponse(
            status_code=503, content={"status": "not_ready", "database": "unavailable"}
        )
    return {"status": "ready", "database": settings.persistence_backend}


@app.get("/health")
def health() -> dict[str, str]:
    return live()


@app.get("/metrics")
def metrics() -> Response:
    try:
        from app.repositories.factory import get_repository

        refresh_repository_metrics(get_repository())
    except Exception as error:  # noqa: BLE001 - metrics survive dependency incidents
        logger.warning("could not refresh durable business metrics: %s", error)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
