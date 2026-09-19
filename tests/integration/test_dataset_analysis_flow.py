from io import BytesIO
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from data_analysis_agent import DataAnalysisAgent
from data_analysis_agent.datasets.errors import DatasetAccessDeniedError, DatasetErrorCode, UploadValidationError
from data_analysis_agent.config.llm import LLMConfig
from tests.fixtures.fake_llm import FakeLLM, dataset_id_from_prompt, yaml_response


def test_quick_analysis_uses_dataset_id_and_loader_instead_of_input_path(
    tmp_path, monkeypatch
):
    source = tmp_path / "private.csv"
    source.write_text("name,value\nA,1\n", encoding="utf-8")
    seen = {}

    def response(fake_llm, call):
        dataset_id = dataset_id_from_prompt(call.prompt)
        seen["dataset_id"] = UUID(dataset_id)
        return yaml_response(
            "generate_code", code=f"df = load_dataset('{dataset_id}')"
        )

    fake_llm = FakeLLM(
        [
            response,
            yaml_response("analysis_complete", final_report="# done"),
            yaml_response("analysis_complete", final_report="# done"),
        ]
    )
    monkeypatch.setattr(
        "data_analysis_agent.agent.core.LLMHelper", lambda config: fake_llm
    )

    from data_analysis_agent import quick_analysis

    result = quick_analysis(
        "分析私有文件",
        files=[str(source)],
        output_dir=tmp_path / "outputs",
        max_rounds=2,
        generate_word_report=False,
    )

    prompts = "\n".join(call.prompt for call in fake_llm.calls)
    assert result["final_report"] == "# done"
    assert str(source) not in prompts
    assert "source_uri" not in prompts
    assert "original_filename" not in prompts
    assert seen["dataset_id"]


class FakeResolver:
    def __init__(self, dataset_id, owner_id):
        self.dataset_id = dataset_id
        self.owner_id = owner_id
        self.profile_calls = []
        self.open_calls = []

    def profile_for_user(self, dataset_id, *, owner_id):
        self.profile_calls.append((dataset_id, owner_id))
        return SimpleNamespace(
            model_dump=lambda mode: {
                "encoding": "utf-8",
                "delimiter": ",",
                "row_count": 1,
                "column_count": 2,
                "columns": [],
                "preview_rows": [],
                "sensitive_fields": [],
            }
        )

    def open_for_user(self, dataset_id, *, owner_id):
        self.open_calls.append((dataset_id, owner_id))
        return BytesIO(b"name,value\nA,1\n")


class LoaderExecutor:
    def __init__(self, output_dir):
        self.output_dir = output_dir
        self.variables = {}

    def set_variable(self, name, value):
        self.variables[name] = value

    def set_sensitive_columns(self, names):
        self.sensitive_columns = set(names)

    def get_environment_info(self):
        return "offline executor"

    def execute_code(self, code):
        namespace = dict(self.variables)
        exec(code, {}, namespace)
        return {"success": True, "output": "", "error": "", "variables": {}}

    def reset_environment(self):
        pass


def test_agent_explicit_dataset_ids_registers_loader_and_owner(monkeypatch, tmp_path):
    dataset_id = uuid4()
    owner_id = uuid4()
    resolver = FakeResolver(dataset_id, owner_id)
    fake_llm = FakeLLM(
        [
            yaml_response("generate_code", code=f"df = load_dataset('{dataset_id}')"),
            yaml_response("analysis_complete", final_report="# done"),
            yaml_response("analysis_complete", final_report="# done"),
        ]
    )
    monkeypatch.setattr("data_analysis_agent.agent.core.LLMHelper", lambda config: fake_llm)
    monkeypatch.setattr("data_analysis_agent.agent.core.CodeExecutor", LoaderExecutor)

    agent = DataAnalysisAgent(
        llm_config=LLMConfig(api_key="offline", base_url="https://offline.invalid", model="fake"),
        output_dir=str(tmp_path / "outputs"),
        max_rounds=1,
        generate_word_report=False,
        dataset_resolver=resolver,
        dataset_owner_id=owner_id,
    )

    result = agent.analyze("offline", dataset_ids=[str(dataset_id)])

    assert result["final_report"] == "# done"
    assert resolver.profile_calls == [(dataset_id, owner_id)]
    assert resolver.open_calls == [(dataset_id, owner_id)]


def test_agent_loader_normalizes_profile_column_names_before_execution(monkeypatch, tmp_path):
    dataset_id = uuid4()
    owner_id = uuid4()
    payload = {
        "encoding": "utf-8",
        "delimiter": ",",
        "row_count": 1,
        "column_count": 2,
        "columns": [
            {"name": "name", "inferred_type": "string"},
            {"name": "email", "inferred_type": "string"},
        ],
        "preview_rows": [{"name": "Alice", "email": "[REDACTED]"}],
        "sensitive_fields": [{
            "column_name": "email",
            "risk_type": "email",
            "detected_by": ["name"],
        }],
    }

    class WhitespaceResolver(FakeResolver):
        def profile_for_user(self, dataset_id, *, owner_id):
            return SimpleNamespace(model_dump=lambda mode: payload)

        def open_for_user(self, dataset_id, *, owner_id):
            return BytesIO(
                b"name, email \nAlice,alice.private@example.test\n"
            )

    resolver = WhitespaceResolver(dataset_id, owner_id)
    fake_llm = FakeLLM([
        yaml_response(
            "generate_code",
            code=(
                f"df = load_dataset('{dataset_id}')\n"
                "assert list(df.columns) == ['name', 'email']\n"
                "assert df.iloc[0]['email'] == 'alice.private@example.test'"
            ),
        ),
        yaml_response("analysis_complete", final_report="# done"),
    ])
    monkeypatch.setattr("data_analysis_agent.agent.core.LLMHelper", lambda config: fake_llm)
    monkeypatch.setattr("data_analysis_agent.agent.core.CodeExecutor", LoaderExecutor)

    result = DataAnalysisAgent(
        llm_config=LLMConfig(api_key="offline", base_url="https://offline.invalid", model="fake"),
        output_dir=str(tmp_path / "outputs"),
        max_rounds=1,
        generate_word_report=False,
        dataset_resolver=resolver,
        dataset_owner_id=owner_id,
    ).analyze("offline", dataset_ids=[dataset_id])

    assert result["analysis_results"][0]["result"]["success"] is True


@pytest.mark.parametrize(
    "kwargs, missing",
    [
        ({"dataset_owner_id": uuid4()}, "dataset_resolver"),
        ({"dataset_resolver": FakeResolver(uuid4(), uuid4())}, "dataset_owner_id"),
    ],
)
def test_quick_analysis_explicit_dataset_ids_requires_access_context(kwargs, missing):
    from data_analysis_agent import quick_analysis

    with pytest.raises(DatasetAccessDeniedError, match=missing):
        quick_analysis("offline", dataset_ids=[uuid4()], **kwargs)


def test_quick_analysis_rejects_files_and_dataset_ids_together(tmp_path):
    from data_analysis_agent import quick_analysis

    source = tmp_path / "input.csv"
    source.write_text("name,value\nA,1\n", encoding="utf-8")

    with pytest.raises(UploadValidationError) as exc_info:
        quick_analysis("offline", files=[str(source)], dataset_ids=[uuid4()])

    assert exc_info.value.code is DatasetErrorCode.INVALID_DATASET_REQUEST


def test_sensitive_dataset_values_never_reach_feedback_or_later_model_prompts(
    tmp_path, monkeypatch, capsys
):
    source = tmp_path / "contacts.csv"
    sensitive_values = ("alice.private@example.test", "13900001234")
    source.write_text(
        "name,email,phone\nAlice," + sensitive_values[0] + "," + sensitive_values[1] + "\n",
        encoding="utf-8",
    )

    def assert_prompt_is_redacted(fake_llm, call):
        if any(value in call.prompt for value in sensitive_values):
            raise AssertionError("sensitive dataset value reached a later model prompt")
        assert "Alice" in call.prompt
        return yaml_response("analysis_complete", final_report="# done")

    def assert_report_prompt_is_redacted(fake_llm, call):
        if any(value in call.prompt for value in sensitive_values):
            raise AssertionError("sensitive dataset value reached the report prompt")
        return yaml_response("analysis_complete", final_report="# report")

    fake_llm = FakeLLM(
        [
            yaml_response(
                "generate_code",
                code="df = load_dataset(dataset_ids[0])\nprint(df)\nprint(df['email'])\ndf",
            ),
            assert_prompt_is_redacted,
            assert_report_prompt_is_redacted,
        ]
    )
    monkeypatch.setattr("data_analysis_agent.agent.core.LLMHelper", lambda config: fake_llm)

    from data_analysis_agent import quick_analysis

    result = quick_analysis(
        "analyze contacts",
        files=[str(source)],
        output_dir=tmp_path / "outputs",
        max_rounds=2,
        generate_word_report=False,
    )

    capsys.readouterr()
    assert len(fake_llm.calls) == 3
    assert result["final_report"] == "# report"
    for call in fake_llm.calls:
        assert not any(value in call.prompt + (call.system_prompt or "") for value in sensitive_values)
    visible_result = repr(result)
    if any(value in visible_result for value in sensitive_values):
        raise AssertionError("sensitive dataset value remained in analysis results")
    assert "Alice" in result["analysis_results"][0]["result"]["output"]
