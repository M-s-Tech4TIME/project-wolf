"""`python -m wolf_database` entry point. Delegates to cli.main()."""

import sys

from wolf_database.cli import main

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
