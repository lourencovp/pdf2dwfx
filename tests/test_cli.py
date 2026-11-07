"""The command-line entry point."""

from pathlib import Path

import pytest

from pdf2dwfx.cli import main
from synthetic import drawing_pdf


def test_default_output_next_to_pdf(tmp_path: Path) -> None:
    pdf = drawing_pdf(tmp_path / "plan.pdf")
    assert main([str(pdf)]) == 0
    assert (tmp_path / "plan.dwfx").is_file()


def test_missing_page_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pdf = drawing_pdf(tmp_path / "plan.pdf")
    assert main([str(pdf), "--page", "5"]) == 1
    assert "page 5 does not exist" in capsys.readouterr().err


def test_network_is_blocked_during_tests() -> None:
    # Guards the guard: if this ever passes a connection, offline tests mean nothing.
    import socket

    with pytest.raises(RuntimeError):
        socket.create_connection(("example.com", 80))
