from __future__ import annotations

import logging
from pathlib import Path

from jukkabot.logging_setup import WSAEINVAL, ProactorTeardownFilter, configure_logging


def _record(message: str, error: BaseException | None) -> logging.LogRecord:
    exc_info = (type(error), error, None) if error is not None else None
    return logging.LogRecord(
        "asyncio", logging.ERROR, __file__, 1, message, (), exc_info
    )


def _winsock_error(code: int) -> OSError:
    error = OSError("An invalid argument was supplied")
    error.winerror = code  # type: ignore[attr-defined]
    return error


_TEARDOWN = "Exception in callback _ProactorBasePipeTransport._call_connection_lost()"


def test_filter_drops_the_proactor_shutdown_noise() -> None:
    record = _record(_TEARDOWN, _winsock_error(WSAEINVAL))
    assert ProactorTeardownFilter().filter(record) is False


def test_filter_keeps_everything_else() -> None:
    keep = ProactorTeardownFilter().filter
    # Same callback, a different Winsock failure: that one is worth seeing.
    assert keep(_record(_TEARDOWN, _winsock_error(10054))) is True
    # Same error code somewhere that is not the known-benign teardown path.
    assert keep(_record("Exception in callback do_real_work()", _winsock_error(WSAEINVAL)))
    assert keep(_record("Task exception was never retrieved", RuntimeError("boom")))
    assert keep(_record("Started playback", None))


def test_configure_logging_writes_to_the_log_file(tmp_path: Path) -> None:
    log_file = tmp_path / "nested" / "jukkabot.log"
    try:
        configure_logging(logging.INFO, log_file)
        logging.getLogger("jukkabot.test").info("hello from the bot")
        logging.shutdown()
        assert "hello from the bot" in log_file.read_text(encoding="utf-8")
    finally:
        configure_logging(logging.WARNING, None)


def test_configure_logging_survives_an_unusable_log_file(tmp_path: Path) -> None:
    # The parent exists as a file, so the log directory cannot be created --
    # that must degrade to console-only rather than stop the bot booting.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    try:
        configure_logging(logging.INFO, blocker / "sub" / "jukkabot.log")
        assert logging.getLogger().handlers
    finally:
        configure_logging(logging.WARNING, None)
