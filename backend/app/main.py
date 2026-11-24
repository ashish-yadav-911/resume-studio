from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import settings
from .db import SessionLocal, init_db
from .models import Run, utcnow
from .routers import conversations, resumes, runs, system
from .security import rate_limiter


def _configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


_configure_logging()
logger = logging.getLogger("resume_studio")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Recover runs left dangling by a previous process/exception so they stop blocking new messages.
    with SessionLocal() as db:
        stale = db.query(Run).filter(Run.status.in_(["queued", "running"])).all()
        for run in stale:
            run.status = "failed"
            run.final_response = run.final_response or "The run was interrupted before completion. Please retry."
            run.updated_at = utcnow()
        db.commit()
    if not settings.llm_enabled:
        logger.warning(
            "OPENAI_API_KEY is not configured: running in deterministic local mode. "
            "Intent routing, scoring and safe rewrites work; free-form LLM features do not."
        )
    if settings.auth_mode in {"dev", "auto"} and not settings.firebase_credentials_path:
        logger.warning("Development authentication is enabled (X-Dev-User header). Do not use in production.")
    if settings.auth_mode == "auto" and settings.firebase_project_id and not settings.firebase_credentials_path:
        logger.info("Firebase auth configured via application default credentials.")
    yield


app = FastAPI(
    title="Resume Studio API",
    version="1.0.0",
    description=(
        "Conversational resume optimization with company research, deterministic job matching, "
        "evidence-validated patches, human approval and immutable versions."
    ),
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Dev-User", "Last-Event-ID", "X-Request-ID"],
    expose_headers=["X-Request-ID", "Retry-After"],
    max_age=600,
)


@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
    started = time.perf_counter()
    identity = request.headers.get("X-Dev-User") or request.headers.get("Authorization", "")
    identity_hash = hashlib.sha256(identity.encode()).hexdigest()[:10] if identity else "anonymous"
    path = request.url.path
    if not (path.startswith("/api/docs") or path == "/api/openapi.json" or path.startswith("/api/redoc")):
        try:
            rate_limiter.check(f"{identity_hash}:{path}")
        except Exception as exc:
            from fastapi import HTTPException

            if isinstance(exc, HTTPException):
                response = JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": exc.detail},
                    headers=exc.headers or {},
                )
                response.headers["X-Request-ID"] = request_id
                return response
            raise
    try:
        response = await call_next(request)
    except Exception:
        duration = int((time.perf_counter() - started) * 1000)
        logger.critical(json.dumps({
            "event": "request_error", "request_id": request_id, "method": request.method,
            "path": path, "status": 500, "duration_ms": duration, "user_hash": identity_hash,
        }))
        raise
    duration = int((time.perf_counter() - started) * 1000)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if path.startswith("/api/") and not path.endswith("/events"):
        response.headers.setdefault("Cache-Control", "no-store")
    logger.info(json.dumps({
        "event": "request", "request_id": request_id, "method": request.method, "path": path,
        "status": response.status_code, "duration_ms": duration, "user_hash": identity_hash,
    }))
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": "Request validation failed.", "errors": exc.errors()[:5]},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):  # pragma: no cover
    logger.exception("Unhandled exception")
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected server error occurred. The request was not completed."},
    )


app.include_router(system.router, prefix=settings.api_prefix)
app.include_router(resumes.router, prefix=settings.api_prefix)
app.include_router(conversations.router, prefix=settings.api_prefix)
app.include_router(runs.router, prefix=settings.api_prefix)


@app.get("/", include_in_schema=False)
def root():
    return {
        "service": settings.app_name,
        "docs": "/api/docs",
        "health": "/api/health",
    }
