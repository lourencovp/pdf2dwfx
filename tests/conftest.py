"""Shared test helpers.

Real project drawings are private and never committed. They live either
inside the project folder (ignored by Git) or next to it. Tests that need
them use the ``private_tests_dir`` fixture, which skips the test
automatically when neither folder exists (for example in CI).
"""

import socket
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FOLDER_NAME = "pdf2dwfx-private-tests"
_CANDIDATES = [
    _REPO_ROOT / _FOLDER_NAME,  # inside the project folder (git-ignored)
    _REPO_ROOT.parent / _FOLDER_NAME,  # next to the project folder
]


@pytest.fixture
def private_tests_dir() -> Path:
    for candidate in _CANDIDATES:
        if candidate.is_dir():
            return candidate
    pytest.skip("private test drawings not available on this machine")


class NetworkBlocked(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """The app must work fully offline: any attempt to reach the network
    during a test fails loudly instead of silently succeeding."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise NetworkBlocked("network access attempted during tests")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
