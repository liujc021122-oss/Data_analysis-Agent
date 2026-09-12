import inspect

import data_analysis_agent


def test_canonical_package_exports_public_objects():
    assert callable(data_analysis_agent.quick_analysis)
    assert data_analysis_agent.ConfigurationError.__module__ == (
        "data_analysis_agent.config.settings"
    )
    assert data_analysis_agent.DataAnalysisAgent.__module__ == (
        "data_analysis_agent.agent.core"
    )
    assert data_analysis_agent.LLMConfig.__module__ == "data_analysis_agent.config.llm"
    assert data_analysis_agent.Settings.__module__ == "data_analysis_agent.config.settings"


def test_quick_analysis_signature_is_stable():
    parameters = inspect.signature(data_analysis_agent.quick_analysis).parameters

    assert list(parameters) == [
        "query",
        "files",
        "output_dir",
        "max_rounds",
        "generate_word_report",
        "settings",
    ]
    assert parameters["query"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameters["files"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert all(
        parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        for name in ("output_dir", "max_rounds", "generate_word_report", "settings")
    )
