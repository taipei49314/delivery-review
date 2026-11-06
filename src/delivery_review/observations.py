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


def render_html(result: dict) -> str:
    """Self-contained, script-free observations; all provider values are text."""
    def esc(value):
        return html.escape("" if value is None else str(value), quote=True)
    rows = []
    jobs = []
    for workflow in result["workflows"]:
        run = workflow.get("run") or {}
        selection = workflow["selection"]
        cells = (workflow["workflow_id"], selection.get("run_id"), selection.get("attempt"),
                 workflow["association"], run.get("conclusion"), run.get("native_state", "unconfirmed"))
        rows.append("<tr>" + "".join("<td>" + esc(value) + "</td>" for value in cells) + "</tr>")
        items = ["<li>" + esc(job.get("name")) + ": " + esc(job.get("status")) + " / " +
                 esc(job.get("conclusion")) + "; association " + esc(job.get("association")) + "</li>"
                 for job in workflow.get("jobs", [])]
        jobs.append("<section><h2>Workflow " + esc(workflow["workflow_id"]) + "</h2><ul>" +
                    "".join(items) + "</ul></section>")
    diagnostics = "".join("<li>" + esc(reason) + "</li>" for reason in result.get("reasons", []))
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>GitHub platform observations</title><style>
body{font:16px/1.6 system-ui,sans-serif;color:#172b3a;background:#f5f7f9;margin:0}
main{max-width:1100px;margin:auto;padding:32px}h1,h2{line-height:1.2}
.scope{border-left:4px solid #62778b;padding:12px;background:white}
dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 24px}dd{margin:0;overflow-wrap:anywhere}
.table{overflow:auto}table{border-collapse:collapse;width:100%;background:white}th,td{text-align:left;border-bottom:1px solid #ced6dd;padding:10px;vertical-align:top}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:16px;font-size:13px}
li{overflow-wrap:anywhere}summary{cursor:pointer}a{color:#164e83}
</style></head><body><main><h1>GitHub platform observations</h1>
<p class="scope">Recorded platform metadata. Tested checkout: not established.
This output makes no delivery readiness or test-execution verdict.</p>
<dl><dt>Collection</dt><dd>""" + esc(result["collection_status"]) + "</dd><dt>Applicability now</dt><dd>" + \
        esc(result["freshness"]) + "</dd><dt>Captured recheck</dt><dd>" + esc(result["captured_freshness"]) + \
        "</dd><dt>Original observation time</dt><dd>" + esc(result["observed_at"]) + "</dd></dl>" + \
        "<div class=\"table\"><table><caption>Selected workflows</caption><thead><tr>" + \
        "".join("<th scope=\"col\">" + label + "</th>" for label in (
            "Workflow", "Selected run", "Attempt", "Association", "Native conclusion", "Native state")) + \
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div><h2>Diagnostics</h2>" + \
        ("<ul>" + diagnostics + "</ul>" if diagnostics else "<p>No collection diagnostics.</p>") + \
        "".join(jobs) + "<details><summary>Full observations and raw references</summary><pre>" + \
        esc(canonical_bytes(result).decode("utf-8")) + "</pre></details></main></body></html>\n"


def write_observations(result: dict, destination) -> None:
    """Write three new files inside an already created result directory."""
    raw = canonical_bytes(result) + b"\n"
    require(len(raw) <= 16 * 1024 * 1024, "observation_output_limit")
    markdown = render_markdown(result).encode("utf-8")
    require(len(markdown) <= 16 * 1024 * 1024, "observation_output_limit")
    document = render_html(result).encode("utf-8")
    require(len(document) <= 16 * 1024 * 1024, "observation_output_limit")
    directory = Path(os.path.abspath(destination))
    _directories(directory)
    _write(directory / "observations.json", raw)
    _write(directory / "observations.md", markdown)
    _write(directory / "observations.html", document)


def replay_observations(source, destination) -> dict:
    capture = verify_capture(source)
    result = map_capture(capture, mode="offline_replay")
    directory = new_output_directory(destination, source=source)
    write_observations(result, directory)
    return result
