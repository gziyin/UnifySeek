from __future__ import annotations

from deepagents import SubAgent, create_deep_agent
from deepagents.backends import StateBackend
from langgraph.graph.state import CompiledStateGraph

from ai_dev_researcher.agents.context import RunContext
from ai_dev_researcher.agents.model import ModelBinding
from ai_dev_researcher.agents.profiles import (
    create_deny_all_filesystem_permissions,
    register_project_profile,
)
from ai_dev_researcher.agents.prompts import (
    DOCUMENT_ANALYST_PROMPT,
    WEB_RESEARCHER_PROMPT,
    build_orchestrator_prompt,
)
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.services.evidence_store import EvidenceStore
from ai_dev_researcher.tools.factory import (
    create_document_tools,
    create_orchestrator_tools,
    create_web_tools,
)


def create_research_agent(
    context: RunContext,
    model_binding: ModelBinding,
    store: EvidenceStore,
    artifacts: ArtifactRepository,
    vector_store=None,
    knowledge_index=None,
    kb_budget=None,
) -> CompiledStateGraph:
    """Create the DeepAgents research graph.

    ``knowledge_index`` is optional and backward compatible: when omitted the
    behavior is unchanged (the document-analyst's search_knowledge_base tool
    simply reports note="indexing" until a shared index is registered).
    ``kb_budget`` is the optional run-scoped KB soft budget (#13); when omitted
    the KB tools behave as before (no call limit).
    """
    register_project_profile(model_binding.spec)
    deny_all = create_deny_all_filesystem_permissions()
    web_tools = create_web_tools(context, store)
    doc_tools = create_document_tools(
        context, store, artifacts, vector_store, knowledge_index, kb_budget=kb_budget
    )
    orchestrator_tools = create_orchestrator_tools(context, store, artifacts)

    subagents: list[SubAgent] = [
        {
            "name": "web-researcher",
            "description": "Search and extract public web evidence for the research question.",
            "system_prompt": WEB_RESEARCHER_PROMPT,
            "tools": web_tools,
            "permissions": deny_all,
        },
        {
            "name": "document-analyst",
            "description": "Read authorized uploaded documents and record document evidence.",
            "system_prompt": DOCUMENT_ANALYST_PROMPT,
            "tools": doc_tools,
            "permissions": deny_all,
        },
    ]

    return create_deep_agent(
        model=model_binding.instance,
        system_prompt=build_orchestrator_prompt(context),
        tools=orchestrator_tools,
        subagents=subagents,
        backend=StateBackend(),
        permissions=deny_all,
    )


def list_configured_subagent_names(agent: CompiledStateGraph) -> list[str]:
    """Best-effort introspection for compatibility tests."""
    names: list[str] = []
    graph = getattr(agent, "get_graph", None)
    if callable(graph):
        try:
            nodes = graph().nodes
            for node_name in nodes:
                if "web-researcher" in node_name or "document-analyst" in node_name:
                    names.append(node_name)
        except Exception:  # noqa: BLE001
            pass
    return sorted(set(names))
