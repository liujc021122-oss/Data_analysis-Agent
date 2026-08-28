from prompts import final_report_system_prompt


def test_final_report_prompt_requires_section_summary_and_key_points():
    assert "【部分总结】" in final_report_system_prompt
    assert "【分析要点】" in final_report_system_prompt
    assert "2-3" in final_report_system_prompt
    assert "每个分析部分" in final_report_system_prompt
