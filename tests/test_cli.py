"""The command-line entry point."""

from pathlib import Path

import pytest

from pdf2dwfx.cli import main
from pdf2dwfx.prepare import UNITS_PER_MM
from synthetic import drawing_pdf


def test_default_output_next_to_pdf(tmp_path: Path) -> None:
    pdf = drawing_pdf(tmp_path / "plan.pdf")
    assert main([str(pdf)]) == 0
    assert (tmp_path / "plan.dwfx").is_file()


def test_missing_page_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pdf = drawing_pdf(tmp_path / "plan.pdf")
    assert main([str(pdf), "--pages", "5"]) == 1
    assert "page 5 does not exist" in capsys.readouterr().err


def test_network_is_blocked_during_tests() -> None:
    # Guards the guard: if this ever passes a connection, offline tests mean nothing.
    import socket

    with pytest.raises(RuntimeError):
        socket.create_connection(("example.com", 80))


def _units(dwfx: Path) -> list[float]:
    """Grid units per metre (or per mm on paper) of the sheet and its viewports."""
    import re
    import zipfile

    z = zipfile.ZipFile(dwfx)
    w2x = z.read(next(n for n in z.namelist() if n.endswith("W2X.xml"))).decode()
    return [float(v) for v in re.findall(r'<(?:Units|Viewport) [^>]*Transform="([\d.]+),', w2x)]


def test_show_scales_lists_suggestions(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from synthetic import scale_pdf

    pdf = scale_pdf(tmp_path / "s.pdf")
    assert main([str(pdf), "--show-scales"]) == 0
    out = capsys.readouterr().out
    assert "1:50 + viewports 1:5" in out and "suggested" in out
    assert not (tmp_path / "s.dwfx").exists()  # nothing converted


def test_scale_set_by_user(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from synthetic import scale_pdf

    pdf = scale_pdf(tmp_path / "s.pdf")
    assert main([str(pdf), "--scale", "1=1:20"]) == 0
    assert "set by you" in capsys.readouterr().out
    assert _units(tmp_path / "s.dwfx") == [UNITS_PER_MM * 50]  # 1 m = 50 mm at 1:20; viewport dropped


def test_viewport_set_by_user(tmp_path: Path) -> None:
    from synthetic import scale_pdf

    pdf = scale_pdf(tmp_path / "s.pdf")
    assert main([str(pdf), "--scale", "1=1:100", "--viewport", "1:225,25,340,125=1:10"]) == 0
    units = _units(tmp_path / "s.dwfx")
    assert units[0] == UNITS_PER_MM * 10 and set(units[1:]) == {UNITS_PER_MM * 100}


def test_no_suggest_measures_paper(tmp_path: Path) -> None:
    from synthetic import scale_pdf

    pdf = scale_pdf(tmp_path / "s.pdf")
    assert main([str(pdf), "--no-suggest"]) == 0
    assert _units(tmp_path / "s.dwfx") == [UNITS_PER_MM]  # paper mm


@pytest.mark.parametrize("args", [["--scale", "1:100"], ["--viewport", "1:2,3=1:5"], ["--viewport", "9:1,2,3,4=1:5"]])
def test_bad_scale_input_is_explained(tmp_path: Path, capsys: pytest.CaptureFixture[str], args: list[str]) -> None:
    pdf = drawing_pdf(tmp_path / "plan.pdf")
    assert main([str(pdf), *args]) == 1
    assert "Error:" in capsys.readouterr().err


def test_several_scales_are_flagged(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from synthetic import scale_pdf

    pdf = scale_pdf(tmp_path / "s.pdf")
    assert main([str(pdf), "--show-scales"]) == 0
    out = capsys.readouterr().out
    assert "several scales on this page" in out


def test_imperial_scale_typed_by_user(tmp_path: Path) -> None:
    from synthetic import scale_pdf

    pdf = scale_pdf(tmp_path / "s.pdf")
    assert main([str(pdf), "--scale", "1=1/4\" = 1'-0\""]) == 0
    assert abs(_units(tmp_path / "s.dwfx")[0] - UNITS_PER_MM * 1000 / 48) < 0.001  # 1:48
