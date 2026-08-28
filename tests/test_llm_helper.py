from config.llm_config import LLMConfig
from utils.llm_helper import LLMHelper


def make_helper(model: str) -> LLMHelper:
    helper = object.__new__(LLMHelper)
    helper.config = LLMConfig(
        api_key="test-key",
        base_url="https://api.deepseek.com",
        model=model,
        temperature=0.1,
        max_tokens=8192,
    )
    return helper


def test_deepseek_chat_request_includes_temperature():
    helper = make_helper("deepseek-chat")

    assert helper._build_request_kwargs() == {
        "max_tokens": 8192,
        "temperature": 0.1,
    }


def test_deepseek_reasoner_request_omits_temperature():
    helper = make_helper("deepseek-reasoner")

    assert helper._build_request_kwargs() == {
        "max_tokens": 8192,
    }
