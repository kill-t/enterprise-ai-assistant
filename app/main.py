import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.core.logging import setup_logging


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):

    # startup
    setup_logging()

    logger.info(
        "Enterprise AI Assistant starting..."
    )

    yield

    # shutdown

    logger.info(
        "Enterprise AI Assistant stopped."
    )


def create_app():

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )


    @app.get("/health")
    async def health_check():

        return {
            "status": "ok"
        }


    return app


app = create_app()