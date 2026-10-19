"""Make `python scripts/<x>.py` work from anywhere: repo root on sys.path and as cwd."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
