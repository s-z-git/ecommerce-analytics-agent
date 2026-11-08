"""执行层超时：DuckDB 2.0 没有 statement_timeout，需要 watchdog + interrupt。"""
from __future__ import annotations

import time
from types import SimpleNamespace

from src.exec import duck


def test_timeout_calls_interrupt(monkeypatch) -> None:
    class FakeCon:
        def __init__(self) -> None:
            self.called = False

        def interrupt(self) -> None:
            self.called = True

    fake = FakeCon()
    monkeypatch.setattr(duck, "LIMITS", SimpleNamespace(timeout_seconds=0.01, max_rows=1000, memory_limit="2GB"))
    timer = duck._start_timeout(fake)
    assert timer is not None
    time.sleep(0.05)
    timer.cancel()
    assert fake.called


def test_zero_timeout_disables_timer(monkeypatch) -> None:
    monkeypatch.setattr(duck, "LIMITS", SimpleNamespace(timeout_seconds=0, max_rows=1000, memory_limit="2GB"))
    assert duck._start_timeout(object()) is None
