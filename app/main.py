"""Фабрика приложения. Запускать с одним worker."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import Settings
from app.db.engine import create_db_engine
from app.db.migrations import upgrade_database
from app.logging import configure_logging
from app.web.routes import router

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
    application.include_router(router)
    return application
