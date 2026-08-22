from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

# Winsock's WSAEINVAL. See ProactorTeardownFilter for why it shows up at all.
WSAEINVAL = 10022

LOG_FILE_MAX_BYTES = 5 * 1024 * 1024
LOG_FILE_BACKUP_COUNT = 5

_CONSOLE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_CONSOLE_DATE_FORMAT = "%H:%M:%S"
_FILE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s [%(threadName)s]: %(message)s"


class ProactorTeardownFilter(logging.Filter):
    """Drop the WinError 10022 asyncio raises while closing a socket.

    On Windows, ``_ProactorBasePipeTransport._call_connection_lost`` calls
    ``socket.shutdown()`` on a socket the OS has already torn down, and Winsock
    answers WSAEINVAL instead of staying quiet. asyncio reports that through its
    exception handler, so an ordinary voice disconnect prints a traceback for a
    connection that closed perfectly well. It is a CPython/Windows wart, not a
    bot fault: there is nothing to fix and nothing to react to, and leaving it in
    trains us to ignore tracebacks that do matter.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        error = record.exc_info[1] if record.exc_info else None
        if not isinstance(error, OSError):
            return True
        if getattr(error, "winerror", None) != WSAEINVAL:
            return True
        return "_call_connection_lost" not in record.getMessage()


def _file_handler(log_file: Path) -> logging.Handler | None:
    """Rotating handler for ``log_file``, or None if it cannot be opened.

    Losing the file log is not worth refusing to start the bot over, so a
    failure here degrades to console-only.
    """
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=LOG_FILE_MAX_BYTES,
            backupCount=LOG_FILE_BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError:
        logging.warning("Could not open log file %s; logging to console only.", log_file)
        return None
    handler.setFormatter(logging.Formatter(_FILE_FORMAT))
    return handler


def configure_logging(level: int | str = logging.INFO, log_file: Path | None = None) -> None:
    """Send logs to the console and, when given, to a rotating file.

    The file log is what makes an intermittent fault reportable: playback
    failures, stream retries and disconnects all happen while nobody is watching
    the terminal, and the console scrollback is gone by the time anyone asks.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    handlers[0].setFormatter(logging.Formatter(_CONSOLE_FORMAT, _CONSOLE_DATE_FORMAT))
    if log_file is not None:
        file_handler = _file_handler(log_file)
        if file_handler is not None:
            handlers.append(file_handler)

    noise_filter = ProactorTeardownFilter()
    for handler in handlers:
        handler.addFilter(noise_filter)

    logging.basicConfig(level=level, handlers=handlers, force=True)
