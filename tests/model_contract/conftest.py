import sys
from pathlib import Path

# Keep this shared contract suite runnable with either the root or agent working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core"))
