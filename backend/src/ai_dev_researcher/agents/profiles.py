from __future__ import annotations

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    register_harness_profile,
)
from deepagents.middleware.filesystem import FilesystemPermission


def register_project_profile(model_spec: str) -> None:
    register_harness_profile(
        model_spec,
        HarnessProfile(
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
            excluded_tools={
                "ls",
                "read_file",
                "write_file",
                "edit_file",
                "delete",
                "glob",
                "grep",
                "execute",
            },
        ),
    )


def create_deny_all_filesystem_permissions() -> list[FilesystemPermission]:
    return [
        FilesystemPermission(
            operations=["ls", "read", "write", "delete", "glob", "grep", "execute"],
            paths=["/**"],
            mode="deny",
        )
    ]
