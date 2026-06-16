from __future__ import annotations
"""Configuracion central de logging para QuantumAnalyzer.

Extends the base rotating-file + console setup with an optional
``DuckDBHandler`` that persists structured logs to the ``audit_logs``
table when the Audit Complex subsystem is configured.
"""


import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_FILE = LOG_DIR / "quantum_analyzer.log"


_SECRET_PATTERN = re.compile(
    r"(?i)(apiKey|apikey|token|access_token|secret|password)=([^&\s\"',}]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)(Authorization:\s*Bearer\s+)([A-Za-z0-9._\-]+)")


def sanitize_log_message(value: object) -> str:
    """Mask common credential shapes before logs reach console/file handlers."""
    text = str(value)
    text = _SECRET_PATTERN.sub(lambda m: f"{m.group(1)}=***", text)
    return _BEARER_PATTERN.sub(lambda m: f"{m.group(1)}***", text)


class SecretSanitizingFilter(logging.Filter):
    @staticmethod
    def _clean_arg(value: object) -> object:
        if isinstance(value, str):
            return sanitize_log_message(value)
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = sanitize_log_message(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._clean_arg(v) for k, v in record.args.items()}
            else:
                record.args = tuple(self._clean_arg(arg) for arg in record.args)
        return True


class SafeRotatingFileHandler(RotatingFileHandler):
    """Rotating handler that tolerates Windows file-lock rollover races."""

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except PermissionError:
            if self.stream is None or self.stream.closed:
                self.stream = self._open()


def get_logger(name: str, *, module: str = "") -> logging.Logger:
    """Devuelve logger rotativo centralizado para modulos del sistema.

    Parameters
    ----------
    name:
        Logger name (typically ``__name__``).
    module:
        Audit module tag (e.g., ``"bingx"``, ``"scanner"``).  When provided
        and the DuckDB handler is configured, logs are tagged with this module
        in the ``audit_logs`` table.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addFilter(SecretSanitizingFilter())

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = SafeRotatingFileHandler(
        filename=LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    # Attach DuckDB handler if audit store is configured
    _try_attach_duckdb_handler(logger, module=module or _module_from_name(name))

    return logger


def _module_from_name(name: str) -> str:
    """Derive a module tag from the logger name."""
    parts = name.split(".")
    if len(parts) >= 2:
        return parts[1]  # e.g. "backend.services.bingx_bot" → "services"
    return parts[0] if parts else "system"


def _try_attach_duckdb_handler(logger: logging.Logger, *, module: str) -> None:
    """Attach a DuckDB handler if the audit store is available."""
    try:
        from backend.audit.structured_logger import DuckDBHandler

        handler = DuckDBHandler(module=module, batch_size=50)
        handler.setLevel(logging.DEBUG)
        logger.addHandler(handler)
    except Exception:
        # Audit subsystem not configured — file+console only
        pass


# Silence verbose third-party loggers at INFO level
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
