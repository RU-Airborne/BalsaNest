from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import *
from core import main

if __name__ == "__main__":
    raise SystemExit(main())
