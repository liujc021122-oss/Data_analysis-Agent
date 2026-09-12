import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_analysis_agent import DataAnalysisAgent, LLMConfig
from data_analysis_agent import cli
from data_analysis_agent.config.settings import ConfigurationError, load_settings
from data_analysis_agent.services import openai_client as openai_client_module
from data_analysis_agent.services.errors import sanitize_text
from data_analysis_agent.services.llm import LLMHelper


SECRET = "configured-api-secret"


def test_quick_analysis_whitespace_output_dir_uses_settings_default(monkeypatch):
    settings = SimpleNamespace(
        output_dir=Path("settings-output"),
        app_env="test",
        llm_config=lambda: "fake-config",
    )
    constructed = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            constructed.update(kwargs)

        def analyze(self, **kwargs):
            return {"ok": True}

    monkeypatch.setattr("data_analysis_agent.agent.core.DataAnalysisAgent", FakeAgent)

    from data_analysis_agent.agent.core import quick_analysis

    assert quick_analysis("offline", output_dir=" \t", settings=settings) == {"ok": True}
    assert constructed["output_dir"] == "settings-output"


def test_cli_whitespace_output_dir_forwards_settings_default(monkeypatch):
    settings = SimpleNamespace(output_dir=Path("settings-output"))
    forwarded = {}

    monkeypatch.setattr(cli, "load_settings", lambda **kwargs: settings)
    monkeypatch.setattr(cli, "configure_logging", lambda settings: None)

    def fake_quick_analysis(**kwargs):
        forwarded.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(cli, "quick_analysis", fake_quick_analysis)

    assert cli.main(["--output-dir", " "]) == 0
    assert forwarded["output_dir"] == settings.output_dir


@pytest.mark.parametrize(
    "text",
    [
        '"api_key": "json-secret"',
        "https://example.invalid/v1?key=query-secret",
        "https://example.invalid/v1?sig=query-secret",
        "https://example.invalid/v1?signature=query-secret",
    ],
)
def test_sanitize_text_redacts_json_and_common_query_credentials(text):
    sanitized = sanitize_text(text)

    assert "secret" not in sanitized
    assert "[REDACTED]" in sanitized


@pytest.mark.asyncio
async def test_fallback_diagnostic_redacts_configured_fallback_secret(monkeypatch, capsys):
    class FakeAPIError(Exception):
        pass

    class FailingCompletions:
        async def create(self, **kwargs):
            raise FakeAPIError("fallback unavailable")

    class FakeClient:
        def __init__(self, base_url):
            self.base_url = base_url
            self.chat = SimpleNamespace(completions=FailingCompletions())

    monkeypatch.setattr(openai_client_module, "APIError", FakeAPIError)
    client = object.__new__(openai_client_module.AsyncFallbackOpenAIClient)
    client.primary_client = FakeClient("https://primary.example/v1")
    client.primary_model_name = "primary-model"
    client.fallback_client = FakeClient(
        "https://fallback.example/v1?credential=fallback-secret"
    )
    client.fallback_api_key = "fallback-secret"
    client.fallback_model_name = "fallback-model"
    client.max_retries_primary = 0
    client.max_retries_fallback = 0
    client.retry_delay_seconds = 0
    client.content_filter_error_code = "1301"
    client.content_filter_error_field = "contentFilter"
    client._closed = False

    with pytest.raises(FakeAPIError):
        await client.chat_completions_create(messages=[])

    visible = capsys.readouterr().out
    assert "fallback-secret" not in visible
    assert "备用 API" in visible


def test_llm_failure_redacts_secret_bearing_exception_from_output(capsys):
    class FailingClient:
        async def chat_completions_create(self, **kwargs):
            raise RuntimeError(
                "provider payload api_key=configured-api-secret "
                "Authorization: Bearer bearer-token"
            )

    helper = object.__new__(LLMHelper)
    helper.config = LLMConfig(
        api_key=SECRET,
        base_url="https://user:password@example.invalid/v1?token=url-token",
        model="fake-model",
    )
    helper.client = FailingClient()

    assert helper.call("offline prompt") == ""

    captured = capsys.readouterr()
    visible = captured.out + captured.err
    assert "LLM调用失败" in visible
    assert SECRET not in visible
    assert "bearer-token" not in visible
    assert "password@example.invalid" not in visible
    assert "url-token" not in visible
    assert "provider payload" not in visible


@pytest.mark.asyncio
async def test_openai_diagnostic_redacts_provider_payload(monkeypatch, capsys):
    class FakeAPIError(Exception):
        pass

    class FailingCompletions:
        async def create(self, **kwargs):
            raise FakeAPIError(
                "provider payload api_key=configured-api-secret "
                "Authorization: Bearer bearer-token"
            )

    class FakeClient:
        chat = SimpleNamespace(completions=FailingCompletions())

    monkeypatch.setattr(openai_client_module, "APIError", FakeAPIError)
    client = object.__new__(openai_client_module.AsyncFallbackOpenAIClient)
    client.max_retries_primary = 0
    client.retry_delay_seconds = 0

    with pytest.raises(FakeAPIError):
        await client._attempt_api_call(
            client=FakeClient(),
            model_name="fake-model",
            messages=[],
            max_retries=0,
            api_name="主",
        )

    visible = capsys.readouterr().out
    assert "不可重试错误" in visible
    assert SECRET not in visible
    assert "bearer-token" not in visible
    assert "provider payload" not in visible


def test_agent_error_feedback_and_report_fallback_redact_secret(tmp_path, monkeypatch, capsys):
    class FailingModel:
        def call(self, **kwargs):
            raise RuntimeError(
                "provider payload api_key=configured-api-secret "
                "https://user:password@example.invalid/v1?token=url-token"
            )

    class FakeExecutor:
        def __init__(self, output_dir):
            self.output_dir = output_dir

        def set_variable(self, name, value):
            pass

        def get_environment_info(self):
            return "offline executor"

    monkeypatch.setattr("data_analysis_agent.agent.core.LLMHelper", lambda config: FailingModel())
    monkeypatch.setattr("data_analysis_agent.agent.core.CodeExecutor", FakeExecutor)

    agent = DataAnalysisAgent(
        llm_config=LLMConfig(
            api_key=SECRET,
            base_url="https://user:password@example.invalid/v1?token=url-token",
            model="fake-model",
        ),
        output_dir=str(tmp_path / "outputs"),
        max_rounds=1,
        generate_word_report=False,
    )

    result = agent.analyze("offline request")
    captured = capsys.readouterr()
    visible = repr(result) + captured.out + captured.err
    report_text = Path(result["report_file_path"]).read_text(encoding="utf-8")

    assert "LLM调用错误" in visible
    assert "报告生成失败" in result["final_report"]
    assert SECRET not in visible + report_text
    assert "bearer-token" not in visible + report_text
    assert "password@example.invalid" not in visible + report_text
    assert "url-token" not in visible + report_text
    assert "provider payload" not in visible + report_text


def test_cli_generic_logging_redacts_exception(monkeypatch, caplog):
    secret = "cli-configured-secret"
    monkeypatch.setattr(
        cli,
        "load_settings",
        lambda **kwargs: SimpleNamespace(output_dir=Path("outputs")),
    )
    monkeypatch.setattr(cli, "configure_logging", lambda settings: None)

    def fail_analysis(**kwargs):
        raise RuntimeError(
            f"provider payload api_key={secret} Authorization: Bearer bearer-token"
        )

    monkeypatch.setattr(cli, "quick_analysis", fail_analysis)

    with caplog.at_level(logging.ERROR, logger="data_analysis_agent"):
        assert cli.main([]) == 1

    assert "Analysis failed" in caplog.text
    assert secret not in caplog.text
    assert "bearer-token" not in caplog.text
    assert "provider payload" not in caplog.text


@pytest.mark.parametrize(
    ("app_env", "expected_output"),
    [("development", Path("outputs")), ("test", Path("outputs/test"))],
)
def test_blank_nonproduction_llm_and_output_values_use_profile_defaults(
    tmp_path, app_env, expected_output
):
    settings = load_settings(
        app_env=app_env,
        environ={
            "OPENAI_API_KEY": " \t",
            "OPENAI_BASE_URL": " \t",
            "OPENAI_MODEL": "\n",
            "OUTPUT_DIR": "  ",
        },
        dotenv_dir=tmp_path,
    )

    assert settings.openai_api_key is None
    assert settings.openai_base_url == "https://api.deepseek.com"
    assert settings.openai_model == "deepseek-chat"
    assert settings.output_dir == expected_output


def test_blank_explicit_output_dir_uses_profile_default(tmp_path):
    settings = load_settings(
        app_env="test",
        environ={},
        dotenv_dir=tmp_path,
        output_dir=" \t",
    )

    assert settings.output_dir == Path("outputs/test")


def test_nonblank_configuration_overrides_remain_effective(tmp_path):
    settings = load_settings(
        app_env="development",
        environ={
            "OPENAI_API_KEY": "configured-key",
            "OPENAI_BASE_URL": "https://offline.example/v1",
            "OPENAI_MODEL": "configured-model",
            "OUTPUT_DIR": "custom-output",
        },
        dotenv_dir=tmp_path,
    )

    assert settings.openai_api_key == "configured-key"
    assert settings.openai_base_url == "https://offline.example/v1"
    assert settings.openai_model == "configured-model"
    assert settings.output_dir == Path("custom-output")


def test_production_blank_required_values_are_named_without_echoing_values(tmp_path):
    with pytest.raises(ConfigurationError) as exc_info:
        load_settings(
            app_env="production",
            environ={
                "OPENAI_API_KEY": "production-key",
                "OPENAI_BASE_URL": " \t",
                "OPENAI_MODEL": "\n",
            },
            dotenv_dir=tmp_path,
        )

    message = str(exc_info.value)
    assert "OPENAI_BASE_URL" in message
    assert "OPENAI_MODEL" in message
    assert "production-key" not in message


def test_readme_documents_unified_cli_and_explicit_files():
    readme = Path(__file__).resolve().parents[1].joinpath("README.md").read_text(
        encoding="utf-8"
    )

    assert "python -m data_analysis_agent" in readme
    assert "data-analysis-agent" in readme
    assert "显式传入输入文件" in readme
    assert "默认使用本地数据 `cpc.csv` / `shop.csv`" not in readme
    assert "Python 3.10+" in readme
    assert "from data_analysis_agent import DataAnalysisAgent, LLMConfig" in readme
    assert "from config.llm_config import LLMConfig" not in readme
    assert readme.index("pip install -e .") < readme.index("data-analysis-agent your_data.csv")
