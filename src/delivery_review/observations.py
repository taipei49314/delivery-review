"""Write explicit platform observations without changing their source capture."""
from __future__ import annotations

import html
import os
from pathlib import Path
import re

from .contracts import ContractError, canonical_bytes, require
from .evidence.capture import verify_capture
from .evidence.store import _directories, _write
from .providers.github_observations import map_capture


def new_output_directory(destination, *, source=None) -> Path:
    destination = Path(os.path.abspath(destination))
    if source is not None:
        source = Path(os.path.abspath(source))
        require(not destination.is_relative_to(source), "output_must_be_outside_capture")
    _directories(destination.parent)
    destination.mkdir(mode=0o700)
    return destination


def _text(value) -> str:
    value = "" if value is None else str(value)
    value = "".join(" " if ord(char) < 32 or ord(char) == 127 else char for char in value)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>-])", r"\\\1", html.escape(value, quote=True))


def render_markdown(result: dict) -> str:
    lines = ["# GitHub platform observations", "",
             "Scope: recorded platform metadata. Tested checkout: not established.", "",
             f"Collection: **{_text(result['collection_status'])}**. "
             f"Applicability now: **{_text(result['freshness'])}**. "
             f"Captured recheck: **{_text(result['captured_freshness'])}**.",
             f"Original observation time: {_text(result['observed_at'])}.", "",
             "This output makes no delivery readiness or test-execution verdict.", "",
             "| Workflow | Selected run | Attempt | Association | Native conclusion | Native state |",
             "| --- | --- | --- | --- | --- | --- |"]
    for workflow in result["workflows"]:
        selection = workflow["selection"]
        run = workflow.get("run") or {}
        cells = (workflow["workflow_id"], selection.get("run_id"), selection.get("attempt"),
                 workflow["association"], run.get("conclusion"), run.get("native_state", "unconfirmed"))
        lines.append("| " + " | ".join(_text(value) for value in cells) + " |")
    lines += ["", "## Diagnostics", ""]
    reasons = result.get("reasons", [])
    lines += ["- " + _text(reason) for reason in reasons] if reasons else ["No collection diagnostics."]
    for workflow in result["workflows"]:
        lines += ["", f"## Workflow {_text(workflow['workflow_id'])}", ""]
        for job in workflow.get("jobs", []):
            lines.append(f"- {_text(job.get('name'))}: {_text(job.get('status'))} / "
                         f"{_text(job.get('conclusion'))}; native state {_text(job.get('native_state', 'unconfirmed'))}; "
                         f"association {_text(job.get('association'))}.")
        lines += ["- " + _text(reason) for reason in workflow.get("reasons", [])]
    lines += ["", "Raw references, native values and per-item diagnostics are retained in observations.json.", ""]
    return "\n".join(lines)


def write_observations(result: dict, destination) -> None:
    """Write two new files inside an already created result directory."""
    raw = canonical_bytes(result) + b"\n"
    require(len(raw) <= 16 * 1024 * 1024, "observation_output_limit")
    markdown = render_markdown(result).encode("utf-8")
    require(len(markdown) <= 16 * 1024 * 1024, "observation_output_limit")
    directory = Path(os.path.abspath(destination))
    _directories(directory)
    _write(directory / "observations.json", raw)
    _write(directory / "observations.md", markdown)


def replay_observations(source, destination) -> dict:
    capture = verify_capture(source)
    result = map_capture(capture, mode="offline_replay")
    directory = new_output_directory(destination, source=source)
    write_observations(result, directory)
    return result
