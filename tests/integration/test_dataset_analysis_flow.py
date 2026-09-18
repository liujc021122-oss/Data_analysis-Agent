from pathlib import Path
from uuid import UUID

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
