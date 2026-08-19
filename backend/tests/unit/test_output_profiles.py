from __future__ import annotations

import json
from pathlib import Path

from ai_dev_researcher.core.config import Settings
from ai_dev_researcher.core.output_profiles import (
    OUTPUT_PROFILES,
    RunBudget,
    get_output_profile,
    resolve_run_budget,
)
from ai_dev_researcher.domain.runs import DEFAULT_OUTPUT_MODE, OutputMode, ResearchRequest


def _settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(
        workspace_root=tmp_path / "ws",
        fake_agent_mode=True,
        **overrides,
    )


def test_default_output_mode_is_medium() -> None:
    assert DEFAULT_OUTPUT_MODE == OutputMode.MEDIUM
    request = ResearchRequest(question="q")
    assert request.output_mode == OutputMode.MEDIUM


def test_output_mode_values() -> None:
    assert OutputMode.SHORT.value == "short"
    assert OutputMode.MEDIUM.value == "medium"
    assert OutputMode.LONG.value == "long"


def test_old_request_json_without_output_mode_still_parses() -> None:
    """旧 request_json 缺 output_mode 字段：model_validate 回退到默认 medium。"""
    legacy = {"question": "q", "max_web_sources": 8}
    request = ResearchRequest.model_validate(legacy)
    assert request.output_mode == OutputMode.MEDIUM


def test_request_to_json_includes_output_mode() -> None:
    request = ResearchRequest(question="q", output_mode=OutputMode.SHORT)
    dumped = request.model_dump(mode="json")
    assert dumped["output_mode"] == "short"
    reparsed = ResearchRequest.model_validate(json.loads(json.dumps(dumped)))
    assert reparsed.output_mode == OutputMode.SHORT


def test_profile_initial_values() -> None:
    """服务端 profile 初始值：short=120s/24 tool/6 KB/3 K 证据、
    medium=300s/40/12/5、long=600s/60/12/8。"""
    assert OUTPUT_PROFILES[OutputMode.SHORT].max_elapsed_seconds == 120.0
    assert OUTPUT_PROFILES[OutputMode.SHORT].max_tool_calls == 24
    assert OUTPUT_PROFILES[OutputMode.SHORT].kb_max_tool_calls == 6
    assert OUTPUT_PROFILES[OutputMode.SHORT].max_k_evidence == 3

    assert OUTPUT_PROFILES[OutputMode.MEDIUM].max_elapsed_seconds == 300.0
    assert OUTPUT_PROFILES[OutputMode.MEDIUM].max_tool_calls == 40
    assert OUTPUT_PROFILES[OutputMode.MEDIUM].kb_max_tool_calls == 12
    assert OUTPUT_PROFILES[OutputMode.MEDIUM].max_k_evidence == 5

    assert OUTPUT_PROFILES[OutputMode.LONG].max_elapsed_seconds == 600.0
    assert OUTPUT_PROFILES[OutputMode.LONG].max_tool_calls == 60
    assert OUTPUT_PROFILES[OutputMode.LONG].kb_max_tool_calls == 12
    assert OUTPUT_PROFILES[OutputMode.LONG].max_k_evidence == 8


def test_get_output_profile_normalizes_unknown_and_none() -> None:
    assert get_output_profile(None).mode == OutputMode.MEDIUM
    assert get_output_profile("unknown").mode == OutputMode.MEDIUM
    assert get_output_profile("").mode == OutputMode.MEDIUM
    assert get_output_profile(OutputMode.LONG).mode == OutputMode.LONG


# ---------------------------------------------------------------------------
# resolve_run_budget：profile 初始值 + settings 全局收紧 + constraints 覆盖
# ---------------------------------------------------------------------------


def test_resolve_run_budget_uses_profile_initial_values() -> None:
    budget = resolve_run_budget(OutputMode.SHORT)
    assert budget == RunBudget(
        max_tool_calls=24,
        max_elapsed_seconds=120.0,
        kb_max_tool_calls=6,
        max_k_evidence=3,
    )


def test_resolve_run_budget_settings_positive_tightens_below_profile(tmp_path: Path) -> None:
    """Settings 正数作为全局收紧上限：effective=min(profile, settings)。"""
    settings = _settings(
        tmp_path,
        agent_max_tool_calls=10,
        agent_max_elapsed_seconds=20.0,
        kb_max_tool_calls=4,
    )
    budget = resolve_run_budget(OutputMode.MEDIUM, settings=settings)
    assert budget == RunBudget(
        max_tool_calls=10,
        max_elapsed_seconds=20.0,
        kb_max_tool_calls=4,
        max_k_evidence=5,
    )

    # KB 同理：short profile KB=6 被 settings 4 收紧。
    short_budget = resolve_run_budget(OutputMode.SHORT, settings=settings)
    assert short_budget.kb_max_tool_calls == 4
    assert short_budget.max_tool_calls == 10
    assert short_budget.max_elapsed_seconds == 20.0
    # K 证据上限不经 settings 收紧，仍按 profile 模式值（medium=5 / short=3）。
    assert short_budget.max_k_evidence == 3


def test_resolve_run_budget_settings_larger_than_profile_keeps_profile(tmp_path: Path) -> None:
    """Settings 大于 profile 时不加宽：仍取 profile 值（min 语义）。"""
    settings = _settings(
        tmp_path,
        agent_max_tool_calls=999,
        agent_max_elapsed_seconds=9000.0,
        kb_max_tool_calls=99,
    )
    budget = resolve_run_budget(OutputMode.LONG, settings=settings)
    assert budget == RunBudget(
        max_tool_calls=60,
        max_elapsed_seconds=600.0,
        kb_max_tool_calls=12,
        max_k_evidence=8,
    )


def test_resolve_run_budget_settings_zero_does_not_bypass_profile(tmp_path: Path) -> None:
    """Settings 0 不放开预算：0 表示不收紧，但不可绕过 output_mode profile（防成本失控）。"""
    settings = _settings(
        tmp_path,
        agent_max_tool_calls=0,
        agent_max_elapsed_seconds=0.0,
        kb_max_tool_calls=0,
    )
    budget = resolve_run_budget(OutputMode.SHORT, settings=settings)
    assert budget == RunBudget(
        max_tool_calls=24,
        max_elapsed_seconds=120.0,
        kb_max_tool_calls=6,
        max_k_evidence=3,
    )


def test_resolve_run_budget_constraints_tighten_below_settings(tmp_path: Path) -> None:
    """constraints 作为更严格的 run 级覆盖：在 profile 与 settings 收紧后再收紧。"""
    settings = _settings(
        tmp_path,
        agent_max_tool_calls=20,
        agent_max_elapsed_seconds=60.0,
    )
    budget = resolve_run_budget(
        OutputMode.MEDIUM,
        constraints=["max_tool_calls=5", "max_elapsed_seconds=30"],
        settings=settings,
    )
    assert budget.max_tool_calls == 5
    assert budget.max_elapsed_seconds == 30.0
    # KB 无 constraint 覆盖，仍按 profile/settings 收紧后的值。
    assert budget.kb_max_tool_calls == 12


def test_resolve_run_budget_constraints_looser_than_settings_stays_tight(tmp_path: Path) -> None:
    """constraints 只允许更严格：比 settings 更松的值不会把预算放开。"""
    settings = _settings(tmp_path, agent_max_tool_calls=10, agent_max_elapsed_seconds=30.0)
    budget = resolve_run_budget(
        OutputMode.MEDIUM,
        constraints=["max_tool_calls=50", "max_elapsed_seconds=100"],
        settings=settings,
    )
    assert budget.max_tool_calls == 10
    assert budget.max_elapsed_seconds == 30.0


def test_resolve_run_budget_zero_constraint_does_not_bypass_profile(tmp_path: Path) -> None:
    """constraints 传 0 不放开预算：仍保持 profile/settings 收紧后的值。"""
    settings = _settings(tmp_path, agent_max_tool_calls=0, agent_max_elapsed_seconds=0.0)
    budget = resolve_run_budget(
        OutputMode.MEDIUM,
        constraints=["max_tool_calls=0", "max_elapsed_seconds=0"],
        settings=settings,
    )
    assert budget == RunBudget(
        max_tool_calls=40,
        max_elapsed_seconds=300.0,
        kb_max_tool_calls=12,
        max_k_evidence=5,
    )


def test_resolve_run_budget_ignores_malformed_constraints(tmp_path: Path) -> None:
    budget = resolve_run_budget(
        OutputMode.MEDIUM,
        constraints=["max_tool_calls=oops", "max_elapsed_seconds=abc", "bogus=1"],
    )
    assert budget == RunBudget(
        max_tool_calls=40,
        max_elapsed_seconds=300.0,
        kb_max_tool_calls=12,
        max_k_evidence=5,
    )


def test_resolve_run_budget_accepts_plain_string_mode() -> None:
    budget = resolve_run_budget("short")
    assert budget.max_tool_calls == 24
    assert budget.max_elapsed_seconds == 120.0
    assert budget.kb_max_tool_calls == 6