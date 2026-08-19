from __future__ import annotations

from dataclasses import dataclass

from ai_dev_researcher.core.config import Settings
from ai_dev_researcher.domain.runs import DEFAULT_OUTPUT_MODE, OutputMode


@dataclass(frozen=True)
class OutputProfile:
    """Per-mode run envelope used as the server-side starting budget.

    数值即「服务端 profile 初始值」（short=120s/24 tool/6 KB/3 K 证据，
    medium=300s/40/12/5，long=600s/60/12/8）。Settings 中的正数作为全局收紧上限
    （effective=min(profile, settings)），KB 同理；0 不放开预算（不绕过 profile，
    防成本失控）。run constraints（max_tool_calls / max_elapsed_seconds）继续作为
    更严格的 run 级覆盖。
    """

    mode: OutputMode
    max_elapsed_seconds: float
    max_tool_calls: int
    kb_max_tool_calls: int
    max_k_evidence: int
    reserve: int = 2


OUTPUT_PROFILES: dict[OutputMode, OutputProfile] = {
    OutputMode.SHORT: OutputProfile(
        mode=OutputMode.SHORT,
        max_elapsed_seconds=120.0,
        max_tool_calls=24,
        kb_max_tool_calls=6,
        max_k_evidence=3,
    ),
    OutputMode.MEDIUM: OutputProfile(
        mode=OutputMode.MEDIUM,
        max_elapsed_seconds=300.0,
        max_tool_calls=40,
        kb_max_tool_calls=12,
        max_k_evidence=5,
    ),
    OutputMode.LONG: OutputProfile(
        mode=OutputMode.LONG,
        max_elapsed_seconds=600.0,
        max_tool_calls=60,
        kb_max_tool_calls=12,
        max_k_evidence=8,
    ),
}


def get_output_profile(mode: OutputMode | str | None) -> OutputProfile:
    """Resolve a mode to its profile; unknown/missing values converge to medium."""
    if isinstance(mode, str):
        try:
            mode = OutputMode(mode)
        except ValueError:
            mode = DEFAULT_OUTPUT_MODE
    if mode is None or mode not in OUTPUT_PROFILES:
        return OUTPUT_PROFILES[DEFAULT_OUTPUT_MODE]
    return OUTPUT_PROFILES[mode]


@dataclass(frozen=True)
class RunBudget:
    """Resolved budget for a run.

    计算顺序：output_mode profile 初始值 → Settings 正数全局收紧（min）→ 更严格的
    run 级 constraints 覆盖。任何一层的 0 都只表示「不收紧」，不会把预算放开。

    ``reserve``：为收尾保留的工具调用数（get_evidence_ledger + submit_research_report），
    探索类工具的实际预算为 max_tool_calls - reserve（batch C）。max_tool_calls 总量不变。
    """

    max_tool_calls: int
    max_elapsed_seconds: float
    kb_max_tool_calls: int
    max_k_evidence: int
    reserve: int = 2


def resolve_run_budget(
    mode: OutputMode | str | None,
    constraints: list[str] | None = None,
    settings: Settings | None = None,
) -> RunBudget:
    """Resolve the run budget from mode profile, settings cap and constraints.

    - profile：每模式默认值（short=24/120/6、medium=40/300/12、long=60/600/12）。
    - settings：正数值作为全局收紧上限（effective=min(profile, settings)）；0/负值
      表示不收紧，但**不会绕过 profile**（不再支持旧「0=不限制」的放开语义）。
    - constraints：max_tool_calls / max_elapsed_seconds 继续作为更严格的 run 级覆盖；
      非正数值视为不生效（不放开预算）。
    """
    profile = get_output_profile(mode)
    max_tool_calls = profile.max_tool_calls
    max_elapsed_seconds = profile.max_elapsed_seconds
    kb_max_tool_calls = profile.kb_max_tool_calls
    max_k_evidence = profile.max_k_evidence

    if settings is not None:
        if settings.agent_max_tool_calls > 0:
            max_tool_calls = min(max_tool_calls, settings.agent_max_tool_calls)
        if settings.agent_max_elapsed_seconds > 0:
            max_elapsed_seconds = min(
                max_elapsed_seconds, settings.agent_max_elapsed_seconds
            )
        if settings.kb_max_tool_calls > 0:
            kb_max_tool_calls = min(kb_max_tool_calls, settings.kb_max_tool_calls)

    for constraint in constraints or []:
        stripped = constraint.strip()
        for sep in ("=", ":"):
            if sep not in stripped:
                continue
            key, value = (part.strip() for part in stripped.split(sep, 1))
            if key == "max_tool_calls":
                parsed = _parse_int(value)
                if parsed is not None and parsed > 0:
                    max_tool_calls = min(max_tool_calls, parsed)
            elif key == "max_elapsed_seconds":
                parsed = _parse_float(value)
                if parsed is not None and parsed > 0:
                    max_elapsed_seconds = min(max_elapsed_seconds, parsed)

    return RunBudget(
        max_tool_calls=max_tool_calls,
        max_elapsed_seconds=max_elapsed_seconds,
        kb_max_tool_calls=kb_max_tool_calls,
        max_k_evidence=profile.max_k_evidence,
        reserve=profile.reserve,
    )


def _parse_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _parse_float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None
