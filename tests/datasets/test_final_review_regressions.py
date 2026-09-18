from io import BytesIO
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from data_analysis_agent import DataAnalysisAgent, LLMConfig, quick_analysis
from data_analysis_agent.config.settings import ConfigurationError, load_settings
from data_analysis_agent.datasets.inspection import CsvInspector
from data_analysis_agent.datasets.models import ColumnProfile, DatasetProfile
from data_analysis_agent.services.errors import sanitize_exception
from tests.fixtures.fake_llm import FakeLLM, yaml_response


def _inspect(payload: bytes, filename: str = "sample.csv"):
    return CsvInspector().inspect(BytesIO(payload), filename=filename)


def test_inspector_rejects_explicit_unsupported_delimiter_in_single_column_shape():
    with pytest.raises(Exception) as exc_info:
        _inspect(b"a:b\n1:2\n")

    assert exc_info.value.code.value == "INVALID_CSV"


def test_inspector_accepts_a_genuine_name_only_single_column_csv():
    profile = _inspect(b"name\nAlice\nBob\n")

    assert profile.delimiter == ","
    assert profile.column_count == 1
    assert [row["name"] for row in profile.preview_rows] == ["Alice", "Bob"]


def test_value_sensitivity_only_scans_first_twenty_non_blank_values():
    rows = [f"{index}\n" for index in range(20)] + ["late@example.com\n"]

    profile = _inspect(("contact\n" + "".join(rows)).encode())

    assert not profile.sensitive_fields


class _ProfileResolver:
    def __init__(self, dataset_id, owner_id, payload, profile):
        self.dataset_id = dataset_id
        self.owner_id = owner_id
        self.payload = payload
        self.profile = profile

    def profile_for_user(self, dataset_id, *, owner_id):
        assert dataset_id == self.dataset_id
        assert owner_id == self.owner_id
        return self.profile

    def open_for_user(self, dataset_id, *, owner_id):
        assert dataset_id == self.dataset_id
        assert owner_id == self.owner_id
        return BytesIO(self.payload)


class _LoaderExecutor:
    def __init__(self, output_dir):
        self.variables = {}

    def set_variable(self, name, value):
        self.variables[name] = value

    def get_environment_info(self):
        return "offline executor"

    def execute_code(self, code):
        namespace = dict(self.variables)
        exec(code, {}, namespace)
        return {"success": True, "output": "", "error": "", "variables": {}}

    def reset_environment(self):
        pass


@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
def test_agent_loader_uses_profile_encoding_and_delimiter(
    monkeypatch, tmp_path, delimiter
):
    dataset_id = uuid4()
    owner_id = uuid4()
    payload = f"name{delimiter}城市\nAlice{delimiter}北京\n".encode("gbk")
    profile = DatasetProfile(
        encoding="gbk",
        delimiter=delimiter,
        row_count=1,
        column_count=2,
        columns=(
            ColumnProfile(
                name="name",
                inferred_type="string",
                non_null_count=1,
                missing_count=0,
                missing_rate=0.0,
            ),
            ColumnProfile(
                name="城市",
                inferred_type="string",
                non_null_count=1,
                missing_count=0,
                missing_rate=0.0,
            ),
        ),
    )
    resolver = _ProfileResolver(dataset_id, owner_id, payload, profile)
    fake_llm = FakeLLM(
        [
            yaml_response(
                "generate_code",
                code=(
                    f"df = load_dataset('{dataset_id}')\n"
                    "assert list(df.columns) == ['name', '城市']\n"
                    "assert df.iloc[0]['城市'] == '北京'"
                ),
            ),
            yaml_response("analysis_complete", final_report="# done"),
        ]
    )
    monkeypatch.setattr("data_analysis_agent.agent.core.LLMHelper", lambda config: fake_llm)
    monkeypatch.setattr("data_analysis_agent.agent.core.CodeExecutor", _LoaderExecutor)

    result = DataAnalysisAgent(
        llm_config=LLMConfig(api_key="offline", base_url="https://offline.invalid", model="fake"),
        output_dir=str(tmp_path / "outputs"),
        max_rounds=1,
        generate_word_report=False,
        dataset_resolver=resolver,
        dataset_owner_id=owner_id,
    ).analyze("offline", dataset_ids=[dataset_id])

    assert result["analysis_results"][0]["result"]["success"] is True


def test_direct_agent_files_uses_same_upload_adapter_without_prompt_path_leak(
    monkeypatch, tmp_path
):
    source = tmp_path / "private.csv"
    source.write_text("name,value\nAlice,1\n", encoding="utf-8")
    fake_llm = FakeLLM(
        [
            lambda fake_llm, call: yaml_response(
                "generate_code",
                code="df = load_dataset(dataset_ids[0])\nassert df.iloc[0]['name'] == 'Alice'",
            ),
            yaml_response("analysis_complete", final_report="# done"),
        ]
    )
    monkeypatch.setattr("data_analysis_agent.agent.core.LLMHelper", lambda config: fake_llm)
    monkeypatch.setattr("data_analysis_agent.agent.core.CodeExecutor", _LoaderExecutor)

    result = DataAnalysisAgent(
        llm_config=LLMConfig(api_key="offline", base_url="https://offline.invalid", model="fake"),
        output_dir=str(tmp_path / "outputs"),
        max_rounds=1,
        generate_word_report=False,
    ).analyze("offline", files=[str(source)])

    assert result["analysis_results"][0]["result"]["success"] is True
    assert str(source) not in "\n".join(call.prompt for call in fake_llm.calls)


def test_production_quick_analysis_files_fails_closed_without_upload_service(
    tmp_path, monkeypatch
):
    settings = load_settings(
        app_env="production",
        environ={
            "OPENAI_API_KEY": "offline-key",
            "OPENAI_BASE_URL": "https://offline.invalid/v1",
            "OPENAI_MODEL": "fake",
            "DATABASE_URL": "mysql+pymysql://user:pass@db/app",
            "STORAGE_ENDPOINT": "https://objects.invalid",
            "STORAGE_BUCKET": "datasets",
        },
        dotenv_dir=tmp_path,
    )
    source = tmp_path / "private.csv"
    source.write_text("name\nAlice\n", encoding="utf-8")
    monkeypatch.setattr(
        "data_analysis_agent.agent.core.DataAnalysisAgent",
        lambda **kwargs: pytest.fail("production files must be rejected before agent construction"),
    )

    with pytest.raises(ConfigurationError, match="production upload service|dataset_ids"):
        quick_analysis("offline", files=[str(source)], settings=settings)
