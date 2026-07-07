"""python -m flopi_db_sync <테이블명> 진입점."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
