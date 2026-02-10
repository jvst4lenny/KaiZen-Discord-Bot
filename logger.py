import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logger(cfg: dict) -> logging.Logger:
    level_str = str(cfg.get("level", "INFO")).upper()
    level = getattr(logging, level_str, logging.INFO)

    logger = logging.getLogger("bot")
    logger.setLevel(level)
    logger.propagate = False

    while logger.handlers:
        logger.handlers.pop()

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if cfg.get("console", True):
        ch = logging.StreamHandler()
        ch.setLevel(level)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

    if cfg.get("file_enabled", True):
        file_path = cfg.get("file_path", "logs/bot.log")
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        max_bytes = int(cfg.get("max_bytes", 5 * 1024 * 1024))
        backup_count = int(cfg.get("backup_count", 5))
        fh = RotatingFileHandler(
            file_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        fh.setLevel(level)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    dlogger = logging.getLogger("discord")
    dlogger.setLevel(level)

    return logger
