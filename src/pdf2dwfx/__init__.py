"""pdf2dwfx: convert vector PDF drawings into DWFx files.

The converter core lives in this package and must stay free of GUI code,
so it can be tested and used on its own.
"""

from importlib.metadata import version

# Read the version from pyproject.toml (single source of truth).
__version__: str = version("pdf2dwfx")
