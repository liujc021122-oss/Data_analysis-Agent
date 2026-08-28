import data_analysis_agent


def test_module_exports_quick_analysis_helper():
    assert callable(data_analysis_agent.quick_analysis)

