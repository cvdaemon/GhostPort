from __future__ import annotations

import logging
import logging.handlers
import re
import sys
from logging import Filter, LogRecord

from ghostport.config.paths import AppPaths

_SECRET_PATTERNS = (
    re.compile(r"(password\s*[=:]\s*)\S+", re.IGNORECASE),
    re.compile(r"(passphrase\s*[=:]\s*)\S+", re.IGNORECASE),
    re.compile(r"(secret_ref\s*[=:]\s*)\S+", re.IGNORECASE),
    re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
        re.IGNORECASE | re.DOTALL,
    ),
)


class RedactionFilter(Filter):
    def filter(self, record: LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = message
        for pattern in _SECRET_PATTERNS:
            if pattern.groups:
                redacted = pattern.sub(r"\1***", redacted)
            else:
                redacted = pattern.sub("***REDACTED KEY***", redacted)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def configure_logging(paths: AppPaths) -> None:
    log_file = paths.logs_dir / "ghostport.log"
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    redaction = RedactionFilter()

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redaction)

    root = logging.getLogger("ghostport")
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(file_handler)
    root.propagate = False

    if not getattr(sys, "frozen", False):
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        stream.addFilter(redaction)
        root.addHandler(stream)

    logging.getLogger("paramiko").setLevel(logging.WARNING)

    def _excepthook(exc_type, exc, tb) -> None:
        logging.getLogger("ghostport").critical(
            "Uncaught exception",
            exc_info=(exc_type, exc, tb),
        )
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _excepthook

    if hasattr(sys, "unraisablehook"):
        def _unraisable(unraisable) -> None:  # type: ignore[no-untyped-def]
            logging.getLogger("ghostport").critical(
                "Unraisable exception",
                exc_info=unraisable.exc_info,
            )

        sys.unraisablehook = _unraisable

    try:
        import threading

        def _thread_hook(args: threading.ExceptHookArgs) -> None:
            logging.getLogger("ghostport").critical(
                "Uncaught thread exception",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )

        threading.excepthook = _thread_hook
    except Exception:
        logging.getLogger("ghostport").warning("Could not install threading.excepthook")
