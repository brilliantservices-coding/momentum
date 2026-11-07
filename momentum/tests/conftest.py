import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("MOM_BACKEND", "local")
os.environ.setdefault("MOM_DATA_DIR", tempfile.mkdtemp(prefix="momtest-"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
