from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

from data_analysis_agent import cli


def make_settings(database_url=None):
    return SimpleNamespace(
        database_url=database_url,
        output_dir=Path("outputs"),
        storage_local_root=Path("storage"),
        max_upload_size=1024,
        app_env="test",
        llm_config=lambda: "offline-config",
    )


def test_cli_dataset_id_requires_owner_before_analysis(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda **kwargs: make_settings("sqlite:///test.db"))
    monkeypatch.setattr(cli, "configure_logging", lambda settings: None)

    assert cli.main(["--dataset-id", str(uuid4())]) == 2
    assert "--dataset-owner-id" in capsys.readouterr().err


def test_cli_dataset_id_requires_database_url_before_analysis(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda **kwargs: make_settings())
    monkeypatch.setattr(cli, "configure_logging", lambda settings: None)

    assert cli.main(
        ["--dataset-id", str(uuid4()), "--dataset-owner-id", str(uuid4())]
    ) == 2
    assert "DATABASE_URL" in capsys.readouterr().err


def test_cli_dataset_ids_forward_owner_and_injected_resolver_without_uri(
    monkeypatch, capsys
):
    dataset_id = uuid4()
    owner_id = uuid4()
    resolver = object()
    settings = make_settings("sqlite:///test.db")
    forwarded = {}
    disposed = []

    class FakeDatabase:
        engine = SimpleNamespace(dispose=lambda: disposed.append(True))
        session_factory = object()

    monkeypatch.setattr(cli, "load_settings", lambda **kwargs: settings)
    monkeypatch.setattr(cli, "configure_logging", lambda settings: None)
    monkeypatch.setattr(cli, "Database", SimpleNamespace(from_settings=lambda value: FakeDatabase()))
    monkeypatch.setattr(cli, "build_dataset_resolver", lambda database, settings: resolver)

    def fake_quick_analysis(**kwargs):
        forwarded.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(cli, "quick_analysis", fake_quick_analysis)

    assert cli.main(
        [
            "--dataset-id", str(dataset_id),
            "--dataset-owner-id", str(owner_id),
        ]
    ) == 0

    assert forwarded["dataset_ids"] == [str(dataset_id)]
    assert forwarded["dataset_owner_id"] == owner_id
    assert forwarded["dataset_resolver"] is resolver
    assert forwarded["files"] is None
    assert "file:///private/not-for-output" not in capsys.readouterr().out
    assert disposed == [True]


def test_cli_dataset_id_disposes_engine_when_analysis_fails(monkeypatch):
    owner_id = uuid4()
    settings = make_settings("sqlite:///test.db")
    disposed = []

    class FakeDatabase:
        engine = SimpleNamespace(dispose=lambda: disposed.append(True))
        session_factory = object()

    monkeypatch.setattr(cli, "load_settings", lambda **kwargs: settings)
    monkeypatch.setattr(cli, "configure_logging", lambda settings: None)
    monkeypatch.setattr(
        cli, "Database", SimpleNamespace(from_settings=lambda value: FakeDatabase())
    )
    monkeypatch.setattr(cli, "build_dataset_resolver", lambda database, settings: object())

    def failing_quick_analysis(**kwargs):
        raise RuntimeError("offline analysis failure")

    monkeypatch.setattr(cli, "quick_analysis", failing_quick_analysis)

    assert cli.main(
        [
            "--dataset-id", str(uuid4()),
            "--dataset-owner-id", str(owner_id),
        ]
    ) == 1

    assert disposed == [True]


def test_cli_dataset_id_rejects_positional_files(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "load_settings", lambda **kwargs: make_settings("sqlite:///test.db"))
    monkeypatch.setattr(cli, "configure_logging", lambda settings: None)
    source = tmp_path / "input.csv"
    source.write_text("name,value\nA,1\n", encoding="utf-8")

    assert cli.main([str(source), "--dataset-id", str(uuid4())]) == 2
    assert "cannot be combined" in capsys.readouterr().err
