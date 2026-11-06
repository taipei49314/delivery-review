"""Pure provider observation mapping; no network or producer authentication."""

from .github_selection import select_workflow_runs

__all__ = ["select_workflow_runs"]
