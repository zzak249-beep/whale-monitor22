"""Logger con colores para consola."""
import logging
import os
import sys

COLORS = {
    "DEBUG":    "\033[36m",
    "INFO":     "\033[32m",
    "WARNING":  "\033[33m",
    "ERROR":    "\033[31m",
    "CRITICAL": "\033[35m",
    "RESET":    "\033[0m",
}


class ColorFormatter(logging.Formatter):
    def format(self, record):
        color = COLORS.get(record.levelname, COLORS["RESET"])
        reset = COLORS["RESET"]
        record.levelname = f"{color}{record.levelname:<8}{reset}"
        record.name      = f"\033[1m{record.name}\033[0m"
        return super().format(record)


def get_logger(name: str) -> logging.Logger:
    import config as cfg
    level = getattr(logging, cfg.LOG_LEVEL.upper(), logging.INFO)

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(level)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(ColorFormatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    ))
    logger.addHandler(ch)

    # File handler
    os.makedirs("logs", exist_ok=True)
    fh = logging.FileHandler("logs/bot.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(fh)
    logger.propagate = False
    return logger
