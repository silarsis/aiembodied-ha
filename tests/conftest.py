"""Pytest configuration and helpers for async tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    """Execute coroutine test functions using a local event loop."""

    test_function: Callable[..., object] = pyfuncitem.obj
    if asyncio.iscoroutinefunction(test_function):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(test_function(**pyfuncitem.funcargs))
        finally:
            loop.close()
        return True
    return None


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers used in the test suite."""

    config.addinivalue_line("markers", "asyncio: mark test as asynchronous")
