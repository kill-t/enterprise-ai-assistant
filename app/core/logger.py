import logging
from pathlib import Path

from app.core.log_filter import RequestIdFilter

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)


def setup_logging():

    formatter = logging.Formatter(
        fmt=(
            "%(asctime)s | "
            "%(levelname)s | "
            "%(name)s | "
            "request_id=%(request_id)s | "
            "%(message)s"
        ),
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    if root_logger.handlers:
        return

    request_filter = RequestIdFilter()

    # ==========================
    # Console
    # ==========================
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(request_filter)

    root_logger.addHandler(console_handler)

    # ==========================
    # App Logger
    # ==========================
    app_handler = logging.FileHandler(
        LOG_DIR / "app.log",
        encoding="utf-8",
    )
    app_handler.setFormatter(formatter)
    app_handler.addFilter(request_filter)

    app_logger = logging.getLogger("app")
    app_logger.setLevel(logging.INFO)
    app_logger.addHandler(app_handler)
    app_logger.propagate = True

    # ==========================
    # LLM Logger
    # ==========================
    llm_handler = logging.FileHandler(
        LOG_DIR / "llm.log",
        encoding="utf-8",
    )
    llm_handler.setFormatter(formatter)
    llm_handler.addFilter(request_filter)

    llm_logger = logging.getLogger("llm")
    llm_logger.setLevel(logging.INFO)
    llm_logger.addHandler(llm_handler)
    llm_logger.propagate = True
