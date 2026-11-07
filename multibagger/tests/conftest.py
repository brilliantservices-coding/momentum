import os
import sys
from pathlib import Path

os.environ.setdefault("MB_BACKEND", "local")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
