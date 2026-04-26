"""
Logger configurado con colores y formato estructurado
"""
import logging
import sys
from datetime import datetime
import config

class ColorFormatter(logging.Formatter):
    COLORS = {
        "DEBUG":    "\033[36m",   # Cyan
        "INFO":     "\033[32m",   # Verde
        "WARNING":  "\033[33m",   # Amarillo
        "ERROR":    "\033[31m",   # Rojo
        "CRITICAL": "\033[35m",   # Magenta
    }
    RESET = "\033[0m"

    def format(self, record):
        color = self.COLORS.get(record.levelname, self.RESET)
        ts    = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        msg   = super().format(record)
        return f"{color}[{ts}] [{record.levelname}] {record.name} │ {record.getMessage()}{self.RESET}"

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    logger.setLevel(level)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(ColorFormatter())
    logger.addHandler(ch)

    # File handler
    fh = logging.FileHandler("logs/bot.log", encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(name)s | %(message)s"
    ))
    logger.addHandler(fh)

    logger.propagate = False
    return logger
