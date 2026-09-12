"""Compatibility bridge for running the source package from a checkout."""

from importlib.util import spec_from_file_location
from pathlib import Path
import sys


_SOURCE_ROOT = Path(__file__).with_name("src")
_SOURCE_PACKAGE = _SOURCE_ROOT / "data_analysis_agent"
_INIT_FILE = _SOURCE_PACKAGE / "__init__.py"
if not _INIT_FILE.is_file():
    raise ImportError(f"Canonical package not found: {_SOURCE_PACKAGE}")

if __name__ == "__main__":
    sys.path.insert(0, str(_SOURCE_ROOT))
    from data_analysis_agent.cli import main
    raise SystemExit(main())

__file__ = str(_INIT_FILE)
__path__ = [str(_SOURCE_PACKAGE)]
__package__ = __name__
__spec__ = spec_from_file_location(
    __name__, str(_INIT_FILE), submodule_search_locations=[str(_SOURCE_PACKAGE)]
)
exec(
    compile(_INIT_FILE.read_text(encoding="utf-8"), str(_INIT_FILE), "exec"),
    globals(),
    globals(),
)
