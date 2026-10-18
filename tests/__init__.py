# Put src/ on the import path so the tests run from a plain checkout, without installing.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
