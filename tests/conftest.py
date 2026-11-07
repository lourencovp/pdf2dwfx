"""Shared test helpers.

Real project drawings are private and never committed. They live either
inside the project folder (ignored by Git) or next to it. Tests that need
them use the ``private_tests_dir`` fixture, which skips the test
automatically when neither folder exists (for example in CI).
"""

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
