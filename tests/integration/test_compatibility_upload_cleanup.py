from pathlib import Path
from uuid import uuid4

import pytest

from data_analysis_agent import DataAnalysisAgent, LLMConfig, quick_analysis
from data_analysis_agent.config.settings import load_settings
from data_analysis_agent.datasets import LocalStorageBackend
from data_analysis_agent.datasets.errors import DatasetErrorCode, UploadValidationError
from tests.fixtures.fake_llm import FakeLLM, yaml_response


class _LifecycleExecutor:
    def __init__(self, output_dir):
        self.variables = {}

    def set_variable(self, name, value):
        self.variables[name] = value

    def set_sensitive_columns(self, names):
        self.sensitive_columns = set(names)

    def get_environment_info(self):
        return "offline executor"

    def execute_code(self, code):
        return {"success": True, "output": "", "error": "", "variables": {}}

    def reset_environment(self):
        pass


def _settings(tmp_path):
    return load_settings(
        app_env="test",
        environ={
            "STORAGE_LOCAL_ROOT": str(tmp_path / "storage"),
            "OUTPUT_DIR": str(tmp_path / "outputs"),
        },
        dotenv_dir=tmp_path,
    )


def _write_valid_csv(path: Path, name="Alice"):
    path.write_text(f"name,value\n{name},1\n", encoding="utf-8")


def _assert_no_dataset_objects(settings):
    root = settings.storage_local_root
    if root.exists() and any(path.is_file() for path in root.rglob("*")):
        raise AssertionError("compatibility dataset objects remain after the call")


@pytest.mark.parametrize("entrypoint", ["quick", "direct"])
def test_compatibility_batch_failure_removes_previously_uploaded_objects(
    tmp_path, monkeypatch, entrypoint
):
    settings = _settings(tmp_path)
    valid = tmp_path / "valid.csv"
    invalid = tmp_path / "invalid.txt"
    _write_valid_csv(valid)
    invalid.write_text("not a supported dataset", encoding="utf-8")
    monkeypatch.setattr("data_analysis_agent.agent.core.load_settings", lambda: settings)
    monkeypatch.setattr(
        "data_analysis_agent.agent.core.LLMHelper", lambda config: FakeLLM([])
    )

    with pytest.raises(UploadValidationError) as exc_info:
        if entrypoint == "quick":
            quick_analysis("offline", files=[str(valid), str(invalid)], settings=settings)
        else:
            DataAnalysisAgent(
                llm_config=LLMConfig(api_key="offline", base_url="https://offline.invalid", model="fake"),
                output_dir=str(tmp_path / "outputs"),
                generate_word_report=False,
            ).analyze("offline", files=[str(valid), str(invalid)])

    assert exc_info.value.code is DatasetErrorCode.UNSUPPORTED_EXTENSION
    _assert_no_dataset_objects(settings)


@pytest.mark.parametrize("entrypoint", ["quick", "direct"])
def test_compatibility_success_removes_temporary_dataset_objects(
    tmp_path, monkeypatch, entrypoint
):
    settings = _settings(tmp_path)
    source = tmp_path / "valid.csv"
    _write_valid_csv(source)
    monkeypatch.setattr("data_analysis_agent.agent.core.load_settings", lambda: settings)

    if entrypoint == "quick":
        class FakeAgent:
            def __init__(self, **kwargs):
                pass

            def analyze(self, **kwargs):
                return {"ok": True}

        monkeypatch.setattr("data_analysis_agent.agent.core.DataAnalysisAgent", FakeAgent)
        result = quick_analysis("offline", files=[str(source)], settings=settings)
        assert result == {"ok": True}
    else:
        fake_llm = FakeLLM(
            [yaml_response("analysis_complete", final_report="# report")]
        )
        monkeypatch.setattr(
            "data_analysis_agent.agent.core.LLMHelper", lambda config: fake_llm
        )
        monkeypatch.setattr(
            "data_analysis_agent.agent.core.CodeExecutor", _LifecycleExecutor
        )
        previous_resolver = object()
        previous_owner = uuid4()
        agent = DataAnalysisAgent(
            llm_config=LLMConfig(api_key="offline", base_url="https://offline.invalid", model="fake"),
            output_dir=str(tmp_path / "outputs"),
            max_rounds=0,
            generate_word_report=False,
            dataset_resolver=previous_resolver,
            dataset_owner_id=previous_owner,
        )
        result = agent.analyze("offline", files=[str(source)])
        assert result["final_report"] == "# report"
        assert agent.dataset_resolver is previous_resolver
        assert agent.dataset_owner_id == previous_owner

    _assert_no_dataset_objects(settings)


def test_quick_analysis_constructor_failure_cleans_uploaded_objects(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    source = tmp_path / "valid.csv"
    _write_valid_csv(source)

    def fail_constructor(**kwargs):
        raise RuntimeError("offline constructor failure")

    monkeypatch.setattr("data_analysis_agent.agent.core.DataAnalysisAgent", fail_constructor)
    with pytest.raises(RuntimeError, match="offline constructor failure"):
        quick_analysis("offline", files=[str(source)], settings=settings)

    _assert_no_dataset_objects(settings)


@pytest.mark.parametrize("entrypoint", ["quick", "direct"])
@pytest.mark.parametrize("outcome", ["success", "batch_failure", "analysis_failure"])
def test_cleanup_failure_warns_safely_and_continues_without_replacing_outcome(
    tmp_path, monkeypatch, caplog, entrypoint, outcome
):
    settings = _settings(tmp_path)
    sources = [tmp_path / "first.csv", tmp_path / "second.csv"]
    for source in sources:
        _write_valid_csv(source)
    if outcome == "batch_failure":
        invalid = tmp_path / "invalid.txt"
        invalid.write_text("invalid", encoding="utf-8")
        sources.append(invalid)
    sentinel = settings.storage_local_root / "datasets" / "unrelated.csv"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("must remain", encoding="utf-8")
    monkeypatch.setattr("data_analysis_agent.agent.core.load_settings", lambda: settings)
    monkeypatch.setattr("data_analysis_agent.agent.core.CodeExecutor", _LifecycleExecutor)
    monkeypatch.setattr(
        "data_analysis_agent.agent.core.LLMHelper",
        lambda config: FakeLLM([yaml_response("analysis_complete", final_report="# report")]),
    )
    if outcome == "analysis_failure":
        def fail_session(*args, **kwargs):
            raise RuntimeError("offline analysis failure")
        monkeypatch.setattr("data_analysis_agent.agent.core.create_session_output_dir", fail_session)

    delete = LocalStorageBackend.delete
    attempts = []

    def fail_first_delete(storage, uri):
        attempts.append(uri)
        if len(attempts) == 1:
            raise OSError(f"private-path={tmp_path}; uri={uri}; password=never-log-this")
        delete(storage, uri)

    monkeypatch.setattr(LocalStorageBackend, "delete", fail_first_delete)

    def invoke():
        files = [str(source) for source in sources]
        if entrypoint == "quick":
            return quick_analysis("offline", files=files, settings=settings, max_rounds=0)
        return DataAnalysisAgent(
            llm_config=LLMConfig(api_key="offline", base_url="https://offline.invalid", model="fake"),
            output_dir=str(tmp_path / "outputs"),
            max_rounds=0,
            generate_word_report=False,
        ).analyze("offline", files=files)

    if outcome == "success":
        assert invoke()["final_report"] == "# report"
    elif outcome == "batch_failure":
        with pytest.raises(UploadValidationError) as exc_info:
            invoke()
        assert exc_info.value.code is DatasetErrorCode.UNSUPPORTED_EXTENSION
    else:
        with pytest.raises(RuntimeError, match="offline analysis failure"):
            invoke()

    assert len(attempts) == 2
    assert sentinel.read_text(encoding="utf-8") == "must remain"
    assert len(list(settings.storage_local_root.rglob("*.csv"))) == 2  # failed delete plus sentinel
    warnings = [record for record in caplog.records if record.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "cleanup" in warnings[0].message.lower()
    assert warnings[0].exc_info is None
    assert str(tmp_path) not in caplog.text
    assert "never-log-this" not in caplog.text
    assert all(uri not in caplog.text for uri in attempts)


@pytest.mark.parametrize("entrypoint", ["quick", "direct"])
def test_compatibility_analysis_failure_removes_temporary_dataset_objects(
    tmp_path, monkeypatch, entrypoint
):
    settings = _settings(tmp_path)
    source = tmp_path / "valid.csv"
    _write_valid_csv(source)
    monkeypatch.setattr("data_analysis_agent.agent.core.load_settings", lambda: settings)
    monkeypatch.setattr(
        "data_analysis_agent.agent.core.LLMHelper", lambda config: FakeLLM([])
    )

    if entrypoint == "quick":
        class FailingAgent:
            def __init__(self, **kwargs):
                pass

            def analyze(self, **kwargs):
                raise RuntimeError("offline analysis failure")

        monkeypatch.setattr("data_analysis_agent.agent.core.DataAnalysisAgent", FailingAgent)
        invoke = lambda: quick_analysis(
            "offline", files=[str(source)], settings=settings
        )
    else:
        monkeypatch.setattr(
            "data_analysis_agent.agent.core.create_session_output_dir",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("offline session failure")
            ),
        )
        agent = DataAnalysisAgent(
            llm_config=LLMConfig(api_key="offline", base_url="https://offline.invalid", model="fake"),
            output_dir=str(tmp_path / "outputs"),
            generate_word_report=False,
        )
        invoke = lambda: agent.analyze("offline", files=[str(source)])

    with pytest.raises(RuntimeError, match="offline .* failure"):
        invoke()

    _assert_no_dataset_objects(settings)
