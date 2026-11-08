"""Crash reports: written locally, nothing private in them, nothing sent."""

import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pdf2dwfx import cli, crash  # noqa: E402
from pdf2dwfx.crash import NEW_ISSUE, issue_url, make_report, report_folder, save_report, scrub  # noqa: E402
from pdf2dwfx.crash_dialog import CrashDialog  # noqa: E402
from pdf2dwfx.gui import MainWindow  # noqa: E402
from synthetic import layers_pdf  # noqa: E402
from test_gui import opened, two_pages  # noqa: E402,F401 (fixture)

SECRET = "Moradia Cliente Silva"  # stands for a private project/file name


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def reports_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Tests never write into the real report folder."""
    folder = tmp_path / "reports"
    monkeypatch.setattr(crash, "report_folder", lambda: folder)
    return folder


def secret_pdf(tmp_path: Path) -> Path:
    folder = tmp_path / SECRET
    folder.mkdir()
    return layers_pdf(folder / f"{SECRET} - Planta.pdf")


def fail_with_path(pdf: Path) -> Exception:
    try:
        raise KeyError(f"broken thing in {pdf} near {pdf.name}")
    except KeyError as error:
        return error


def test_report_has_numbers_but_no_names(tmp_path: Path) -> None:
    pdf = secret_pdf(tmp_path)
    report = make_report(fail_with_path(pdf), pdf, [str(tmp_path / "out" / f"{SECRET}.dwfx")])
    assert "App version:" in report and "System:" in report
    assert "PDF pages: 1" in report and "PDF layers: 5" in report and "210 x 297 mm (1x)" in report
    assert "KeyError" in report and "test_crash.py" in report  # where it happened
    assert SECRET.lower() not in report.lower()
    assert str(Path.home()) not in report


def test_scrub_removes_other_paths_but_keeps_program_files() -> None:
    text = scrub('File "C:\\x\\pdf2dwfx\\convert.py", line 3\nOSError: D:\\Obras\\Silva\\a.pdf and /home/ana/obra/b.pdf', [])
    assert "convert.py" in text
    assert "Obras" not in text and "obra" not in text and text.count("<path>") == 2


def test_report_saved_locally(tmp_path: Path, reports_in_tmp: Path) -> None:
    saved = save_report("hello")
    assert saved.parent == reports_in_tmp and saved.read_text(encoding="utf-8") == "hello"
    assert save_report("again") != saved  # never overwrites an earlier report


def test_report_folder_is_the_users_own() -> None:
    real = report_folder()  # imported before the tests replaced it
    assert real.is_absolute() and "pdf2dwfx" in real.parts
    assert Path.home() in real.parents or "LOCALAPPDATA" in os.environ


def test_issue_link_holds_only_the_summary(tmp_path: Path) -> None:
    pdf = secret_pdf(tmp_path)
    report = make_report(fail_with_path(pdf), pdf)
    url = issue_url(report)
    assert url.startswith(NEW_ISSUE + "?") and len(url) < 6000
    query = parse_qs(urlparse(url).query)
    assert query["title"][0].startswith("Crash: KeyError")
    assert "PDF pages: 1" in query["body"][0] and "paste" in query["body"][0]
    assert SECRET.lower() not in url.lower()


def test_crash_window_buttons(app: QApplication, tmp_path: Path) -> None:
    saved = save_report("the report")
    dialog = CrashDialog("the report", saved)
    opened_urls: list[str] = []
    dialog.open_url = lambda url: opened_urls.append(url.toString())
    dialog.github_button.click()
    assert QApplication.clipboard().text() == "the report"
    assert opened_urls[-1].startswith(NEW_ISSUE)
    dialog.folder_button.click()
    assert opened_urls[-1].startswith("file:") and saved.parent.name in opened_urls[-1]


def test_unexpected_error_during_conversion_opens_crash_window(app: QApplication, two_pages: Path, tmp_path: Path) -> None:  # noqa: F811
    window = opened(MainWindow(), two_pages, app)
    shown: list[str] = []
    window.show_crash = lambda report, saved: shown.append(report)
    window.scales[1] = "not a scale"  # a programming mistake, deep inside the conversion
    window.start_convert(tmp_path / "out.dwfx")
    window.converter.wait()
    app.processEvents()
    assert len(shown) == 1 and "AttributeError" in shown[0]
    assert "two.pdf" not in shown[0] and "out.dwfx" not in shown[0]
    assert window.converter is None and window.convert_button.isEnabled()


def test_command_line_saves_a_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], reports_in_tmp: Path) -> None:
    pdf = secret_pdf(tmp_path)

    def broken(*args, **kwargs):
        raise KeyError("unexpected")

    monkeypatch.setattr(cli, "convert", broken)
    assert cli.main([str(pdf), "--no-suggest"]) == 2
    err = capsys.readouterr().err
    assert "crash report was saved" in err and NEW_ISSUE in err
    (report,) = reports_in_tmp.iterdir()
    text = report.read_text(encoding="utf-8")
    assert "KeyError" in text and SECRET.lower() not in text.lower()
