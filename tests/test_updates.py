"""Check for updates: asks GitHub only when the user clicks, and copes
with no connection. Tests never reach the network (see conftest.py)."""

import io
import json
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pdf2dwfx import __version__, updates  # noqa: E402
from pdf2dwfx.gui import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def answer(tag: str = "v9.0.0", page: str = "https://github.com/lourencovp/pdf2dwfx/releases/tag/v9.0.0") -> bytes:
    return json.dumps({"tag_name": tag, "html_url": page}).encode()


@pytest.mark.parametrize(
    "latest, current, newer",
    [
        ("0.2.3", "0.2.2", True),
        ("v0.2.10", "0.2.9", True),  # numbers, not text: 10 > 9
        ("0.3", "0.2.9", True),
        ("0.3", "0.3.0", False),
        ("0.2.2", "0.2.2", False),
        ("0.2.1", "0.2.2", False),
        ("1.0.0", "0.9.9", True),
    ],
)
def test_is_newer(latest: str, current: str, newer: bool) -> None:
    assert updates.is_newer(latest, current) is newer


def test_read_release() -> None:
    release = updates.read_release(answer())
    assert release == updates.Release("9.0.0", "https://github.com/lourencovp/pdf2dwfx/releases/tag/v9.0.0")


def test_only_this_projects_pages_are_opened() -> None:
    release = updates.read_release(answer(page="https://example.com/download"))
    assert release.page == updates.RELEASES_PAGE


@pytest.mark.parametrize("bad", [b"not json", b"{}", b'{"tag_name": "nightly"}', b"[]"])
def test_unreadable_answer(bad: bytes) -> None:
    with pytest.raises(updates.UpdateCheckFailed):
        updates.read_release(bad)


def test_no_connection_is_a_plain_failure() -> None:
    # The network is blocked in tests, like a computer that is offline.
    with pytest.raises(updates.UpdateCheckFailed):
        updates.latest_release()


def test_request_names_the_app_and_nothing_else(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = []

    def fake_urlopen(request, timeout, context):
        assert context.verify_mode == updates.ssl.CERT_REQUIRED  # certificates always checked
        sent.append(request)
        return io.BytesIO(answer())

    monkeypatch.setattr(updates.urllib.request, "urlopen", fake_urlopen)
    assert updates.latest_release().version == "9.0.0"
    (request,) = sent
    assert request.full_url == "https://api.github.com/repos/lourencovp/pdf2dwfx/releases/latest"
    assert request.data is None  # nothing is sent
    assert request.get_header("User-agent") == f"pdf2dwfx/{__version__}"


def test_window_never_checks_by_itself(app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updates, "latest_release", lambda: pytest.fail("checked without a click"))
    window = MainWindow()
    app.processEvents()
    assert window.update_checker is None


def clicked(window: MainWindow, app: QApplication) -> None:
    """Click the button and wait for the background check to be handled."""
    window.update_button.click()
    assert not window.update_button.isEnabled()
    deadline = time.monotonic() + 10
    while window.update_checker is not None and time.monotonic() < deadline:
        app.processEvents()
    assert window.update_checker is None
    assert window.update_button.isEnabled()


def recording(window: MainWindow, answer_yes: bool) -> tuple[list, list, list]:
    told, asked, opened = [], [], []
    window.tell = lambda title, text: told.append(text)
    window.ask = lambda title, text, yes: asked.append(text) or answer_yes
    window.open_url = opened.append
    return told, asked, opened


def test_newer_version_offers_the_download_page(app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    release = updates.Release("9.0.0", "https://github.com/lourencovp/pdf2dwfx/releases/tag/v9.0.0")
    monkeypatch.setattr(updates, "latest_release", lambda: release)
    window = MainWindow()
    told, asked, opened = recording(window, answer_yes=True)
    clicked(window, app)
    assert not told and "9.0.0 is available" in asked[0]
    assert opened == [release.page]


def test_declining_opens_nothing(app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updates, "latest_release", lambda: updates.Release("9.0.0", updates.RELEASES_PAGE))
    window = MainWindow()
    _, asked, opened = recording(window, answer_yes=False)
    clicked(window, app)
    assert asked and not opened


def test_already_up_to_date(app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updates, "latest_release", lambda: updates.Release(__version__, updates.RELEASES_PAGE))
    window = MainWindow()
    told, asked, opened = recording(window, answer_yes=True)
    clicked(window, app)
    assert "latest version" in told[0] and not asked and not opened


def test_offline_click_explains_and_offers_the_page(app: QApplication) -> None:
    window = MainWindow()  # the real check, with the network blocked
    _, asked, opened = recording(window, answer_yes=True)
    clicked(window, app)
    assert "Could not check for updates" in asked[0]
    assert opened == [updates.RELEASES_PAGE]
