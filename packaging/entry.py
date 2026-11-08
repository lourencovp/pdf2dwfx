"""Start of the packaged app (the .exe, .app or AppImage).

Opens the app window. ``--cli`` instead runs the command-line converter
with the remaining arguments; the release checks use it to convert a test
drawing with the network blocked.
"""

import multiprocessing
import sys

if __name__ == "__main__":
    # Must come first: a worker process (see parallel.py) starts this same
    # program again, and this line makes it do its work instead of opening a window.
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        from pdf2dwfx.cli import main as cli_main

        sys.exit(cli_main(sys.argv[2:]))
    from pdf2dwfx.gui import main

    sys.exit(main(sys.argv))
