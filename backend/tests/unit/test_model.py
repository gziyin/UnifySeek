from langchain_deepseek import ChatDeepSeek

from ai_dev_researcher.agents import model as model_module


def test_clone_for_structured_output_disables_thinking_without_mutating_source():
    assert hasattr(model_module, "clone_for_structured_output")

    source = ChatDeepSeek(
        model="deepseek-v4-flash",
        api_key="test-key",
        extra_body={
            "trace_id": "keep",
            "thinking": {"type": "enabled"},
        },
    )

    clone = model_module.clone_for_structured_output(source)

    assert clone is not source
    assert clone.extra_body == {
        "trace_id": "keep",
        "thinking": {"type": "disabled"},
    }
    assert source.extra_body == {
        "trace_id": "keep",
        "thinking": {"type": "enabled"},
    }


def test_clone_for_structured_output_handles_default_extra_body():
    source = ChatDeepSeek(
        model="deepseek-v4-flash",
        api_key="test-key",
    )

    clone = model_module.clone_for_structured_output(source)

    assert clone.extra_body == {"thinking": {"type": "disabled"}}
    assert source.extra_body is None
