import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def setup_logging(log_file: Path | None = None, level: int = logging.INFO) -> None:
    log_path = log_file or Path.cwd() / "logs" / "conversao.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    formatter = logging.Formatter(LOG_FORMAT)

    if not any(getattr(handler, "_autoextrato_console", False) for handler in root_logger.handlers):
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        console_handler._autoextrato_console = True
        root_logger.addHandler(console_handler)

    log_file_resolved = log_path.resolve()
    if not any(
        getattr(handler, "_autoextrato_file", None) == log_file_resolved
        for handler in root_logger.handlers
    ):
        file_handler = RotatingFileHandler(
            log_file_resolved,
            maxBytes=5_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        file_handler._autoextrato_file = log_file_resolved
        root_logger.addHandler(file_handler)
