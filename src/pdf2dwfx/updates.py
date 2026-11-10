"""Check GitHub for a newer version, only when the user asks.

This is the app's single exception to working offline: it happens only
when the user clicks "Check for updates", never by itself, and the request
carries nothing about the user or their drawings. GitHub is asked for the
latest release; the answer is its version number and the page to download
it from. Downloading is left to the user, in their browser.

Standard library only (no networking packages), and no GUI code, so the
window decides how to show the answer.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import urllib.request
from dataclasses import dataclass

from . import __version__

REPOSITORY = "lourencovp/pdf2dwfx"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases/latest"
TIMEOUT = 15  # seconds; a slow or missing connection must not hang the check


class UpdateCheckFailed(Exception):
    """GitHub could not be reached, or its answer could not be read."""


@dataclass(frozen=True)
class Release:
    version: str  # e.g. "0.2.3", without the "v" of the tag
    page: str  # the release's download page on GitHub


def version_numbers(text: str) -> tuple[int, ...]:
    """'v0.2.10' -> (0, 2, 10), so versions compare as numbers (10 > 9)."""
    match = re.fullmatch(r"v?(\d+(?:\.\d+)*)", text.strip())
    if not match:
        raise ValueError(f"not a version number: {text!r}")
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(latest: str, current: str = __version__) -> bool:
    # Pad with zeros so "0.3" and "0.3.0" count as the same version.
    a, b = version_numbers(latest), version_numbers(current)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def read_release(answer: bytes) -> Release:
    """The latest release from GitHub's answer (JSON)."""
    try:
        data = json.loads(answer)
        tag = data["tag_name"]
        version_numbers(tag)  # refuse anything that is not a version
        page = data.get("html_url") or RELEASES_PAGE
    except (ValueError, KeyError, TypeError) as error:
        raise UpdateCheckFailed("GitHub's answer could not be read") from error
    # Only ever send the user to this project's own pages.
    if not page.startswith(f"https://github.com/{REPOSITORY}/"):
        page = RELEASES_PAGE
    return Release(version=tag.lstrip("v"), page=page)


# Where macOS and the main Linux families keep their trusted certificates.
_CERTIFICATE_FILES = (
    "/etc/ssl/cert.pem",  # macOS, Arch, Alpine
    "/etc/ssl/certs/ca-certificates.crt",  # Debian, Ubuntu
    "/etc/pki/tls/certs/ca-bundle.crt",  # Fedora, RHEL
    "/etc/ssl/ca-bundle.pem",  # openSUSE
)


def tls_context() -> ssl.SSLContext:
    """Certificate checking as usual. The Python inside the packaged app may
    not know where macOS or Linux keeps its certificates (Windows is always
    found), so if it found none, load the system's file directly."""
    context = ssl.create_default_context()
    if not context.cert_store_stats()["x509_ca"]:
        for path in _CERTIFICATE_FILES:
            if os.path.isfile(path):
                context.load_verify_locations(cafile=path)
                break
    return context


def latest_release() -> Release:
    """Ask GitHub for the latest release. Raises UpdateCheckFailed."""
    request = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            # GitHub requires a User-Agent; it names the app and nothing else.
            "User-Agent": f"pdf2dwfx/{__version__}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=tls_context()) as response:
            return read_release(response.read(1_000_000))
    except UpdateCheckFailed:
        raise
    except Exception as error:  # no connection, timeout, certificate, HTTP error...
        raise UpdateCheckFailed(str(error) or type(error).__name__) from error
