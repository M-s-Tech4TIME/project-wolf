"""Module entry point for `python -m wolf_cert`.

Mirrors the console-script entry point declared in `pyproject.toml`
(`wolf-cert = "wolf_cert.cli:main"`) so both invocation forms work
identically.
"""

import sys

from .cli import main

if __name__ == "__main__":  # pragma: no cover — trivial dispatch
    sys.exit(main())
