import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from sqlalchemy import select

from app.api import agents, auth, datasets, insights, meta, projects, providers, rag, regression, runs, security, suites
from app.core import errors
from app.core.config import get_settings
from app.core.db import session_factory
from app.core.redact import install_log_redaction
from app.core.security import hash_password
from app.models import User

log = logging.getLogger("testlab")


def seed_admin() -> None:
    """Create the first user from ADMIN_EMAIL/ADMIN_PASSWORD if no users exist (decision D7)."""
    s = get_settings()
    if not s.admin_password:
        return
    with session_factory()() as db:
        if db.scalar(select(User.id).limit(1)) is None:
            db.add(User(email=s.admin_email.lower(), password_hash=hash_password(s.admin_password)))
            db.commit()
            log.warning("Seeded admin user %s", s.admin_email)


@asynccontextmanager
async def lifespan(_: FastAPI):
    seed_admin()
    yield


def create_app() -> FastAPI:
    install_log_redaction()
    app = FastAPI(
        title="TestMyLLM API", version="0.1.0", openapi_url="/api/v1/openapi.json", docs_url="/api/docs",
        lifespan=lifespan,
    )
    errors.install(app)
    v1 = APIRouter(prefix="/api/v1")
    for r in (auth.router, projects.router, providers.router, runs.router, datasets.router, suites.router, rag.router, regression.router, security.router, insights.router, agents.router, meta.router):
        v1.include_router(r)
    app.include_router(v1)

    @app.get("/api/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
