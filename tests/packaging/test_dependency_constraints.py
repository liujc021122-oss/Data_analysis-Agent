from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_sqlalchemy_is_constrained_below_the_known_enum_regression():
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    constraints = (ROOT / "requirements" / "constraints-py312.txt").read_text(
        encoding="utf-8"
    )
    assert '"SQLAlchemy>=2.0,<2.1"' in metadata
    assert "SQLAlchemy<2.1" in constraints


def test_mypy_is_available_through_the_dev_extra():
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    constraints = (ROOT / "requirements" / "constraints-py312.txt").read_text(
        encoding="utf-8"
    )
    assert '"mypy>=1.11,<2.0"' in metadata
    assert "mypy==1.20.2" in constraints
