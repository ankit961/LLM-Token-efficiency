"""Shared test fixtures.

The reducer hook now resolves the client version at RUNTIME via `doctor.live_client_version()`
(a `claude --version` probe, cached). To keep tests deterministic and hermetic — independent of
whatever `claude` (if any) is installed on the test/CI machine, and without touching the real
`~/.contextruntime` cache — pin the live version to the confirmed allowlist value by default.
A test that specifically exercises an *unconfirmed* live version overrides `CR_LIVE_CLIENT_VERSION`
itself (its own `monkeypatch.setenv` wins over this autouse default).
"""
import pytest

from contextruntime import doctor


@pytest.fixture(autouse=True)
def _pin_live_client_version(monkeypatch):
    (confirmed,) = tuple(doctor.CONFIRMED_OUTPUT_REPLACEMENT_VERSIONS)
    monkeypatch.setenv("CR_LIVE_CLIENT_VERSION", confirmed)
    yield
