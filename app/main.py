"""Фабрика приложения. Запускать с одним worker."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles

from app.auth.routes import router as auth_router
from app.config import Settings
from app.db.engine import create_db_engine
from app.db.migrations import upgrade_database
from app.logging import configure_logging
from app.web.birthdays import router as birthday_router
from app.web.routes import router
from app.web.security import SecurityHeadersMiddleware

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        path = settings.data_dir / "happyday.db"
        backup = await run_in_threadpool(upgrade_database, path)
        if backup:
            logger.info("Перед миграцией создан backup: %s", backup.name)
        engine = create_db_engine(path)
        application.state.db_engine = engine
        application.state.ready = True
        logger.info("HappyDay запущен")
        try:
            yield
        finally:
            application.state.ready = False
            await run_in_threadpool(engine.dispose)
            logger.info("HappyDay остановлен")

    application = FastAPI(
        title="HappyDay",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.state.settings = settings
    application.state.ready = False
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    application.add_middleware(SecurityHeadersMiddleware)
    application.include_router(router)
    application.include_router(auth_router)
    application.include_router(birthday_router)
    application.mount(
        "/static", StaticFiles(directory=str(Path(__file__).parents[1] / "static")), name="static"
    )
    return application
