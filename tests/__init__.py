# Put src/ on the import path so the tests run from a plain checkout, without installing.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Unit tests run offline: a test that reaches for a real installer script fails loudly
# instead of downloading and running it.
from ornith_launcher.integrations import installers  # noqa: E402


def _no_network(url: str) -> bytes:
    raise AssertionError(f"unit tests must not download {url}")


installers.fetch_script = _no_network
