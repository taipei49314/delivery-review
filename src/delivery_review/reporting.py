"""Render a validated contract-v1 report without creating new evidence claims."""
import html
import re

from .contracts import canonical_bytes, validate_report


def _plain(value):
    text = "" if value is None else str(value)
    return "".join(" " if ord(char) < 32 or ord(char) == 127 else char for char in text)


def _md(value):
    return re.sub(r"([\\`*_{}\[\]()#+.!|>-])", r"\\\1", html.escape(_plain(value), quote=True))


def _html(value):
    return html.escape(_plain(value), quote=True)


def render_report_markdown(report: dict, policy: dict) -> str:
    validate_report(report, policy)
    subject = report["subject"]
    state = report["state"]
    lines = ["# Delivery review", "", "Ready for human review: **" + str(report["ready_for_review"]).lower() + "**.",
             "This decision covers the declared evidence policy; it does not prove program correctness.", "",
             "| Evidence | Freshness | Policy | Exit |", "| --- | --- | --- | --- |",
             "| " + " | ".join(_md(value) for value in (state["evidence"], state["freshness"], state["policy"], report["exit_code"])) + " |",
             "", f"Subject: {_md(subject['repository_full_name'])} PR {_md(subject['pr_number'])}.",
             f"Head: {_md(subject['head_sha'])}.", f"Base tip: {_md(subject['base_tip_sha'])}.",
             f"Merge base: {_md(subject['merge_base_sha'])}.", f"Observed: {_md(report['observed_at'])}; mode: {_md(report['mode'])}.",
             f"Policy: {_md(report['policy']['policy_id'])}; SHA-256: {_md(report['policy']['canonical_sha256'])}.",
             "", "## Required and optional checks", "",
             "| Check | Required | Outcome | Effect | Evidence | Reasons |", "| --- | --- | --- | --- | --- | --- |"]
    for item in report["obligations"]:
        values = (item["id"], item["required"], item["outcome"], item["effect"],
                  ", ".join(item["evidence_ids"]), ", ".join(item["reason_codes"]))
        lines.append("| " + " | ".join(_md(value) for value in values) + " |")
    lines += ["", "## Actions", ""]
    for item in report["action_items"]:
        lines.append("- " + _md(item["id"]) + ": " + _md(item.get("message", item.get("title", ""))))
    if not report["action_items"]:
        lines.append("No action items recorded.")
    for engine in report["engines"]:
        lines += ["", "## " + _md(engine["name"]), "",
                  "Native verdict: " + _md(engine["verdict"]) + "; exit: " + _md(engine["exit_code"]) +
                  "; status: " + _md(engine["status"]) + "; coverage: " + _md(engine["coverage"]["status"]) + ".",
                  "", "| Rule | Severity | Disposition | Fingerprint |", "| --- | --- | --- | --- |"]
        for finding in engine["findings"]:
            lines.append("| " + " | ".join(_md(finding[key]) for key in ("rule", "severity", "disposition", "fingerprint")) + " |")
        for detail in engine["coverage"]["details"]:
            lines.append("- Coverage detail: " + _md(detail))
    lines += ["", "## Limitations", ""] + ["- " + _md(item) for item in report["limitations"]]
    lines += ["", "Original native reports, proof bytes and their digests are retained in the evidence bundle.", ""]
    return "\n".join(lines)


def render_report_html(report: dict, policy: dict) -> str:
    validate_report(report, policy)
    def table(labels, rows):
        return '<div class="table"><table><thead><tr>' + "".join('<th scope="col">' + _html(x) + '</th>' for x in labels) + \
            '</tr></thead><tbody>' + "".join('<tr>' + "".join('<td>' + _html(x) + '</td>' for x in row) + '</tr>' for row in rows) + \
            '</tbody></table></div>'
    subject = report["subject"]
    state = report["state"]
    sections = ['<h1>Delivery review</h1><p class="decision">Ready for human review: <strong>' +
                str(report["ready_for_review"]).lower() + '</strong></p>',
                '<p>This decision covers the declared evidence policy; it does not prove program correctness.</p>',
                table(('Evidence', 'Freshness', 'Policy', 'Exit'), [(state["evidence"], state["freshness"], state["policy"], report["exit_code"])]),
                '<h2>Subject</h2>', table(('Field', 'Value'), [(key, subject[key]) for key in (
                    'repository_full_name', 'pr_number', 'head_sha', 'base_tip_sha', 'merge_base_sha')]),
                '<p>Observed: ' + _html(report['observed_at']) + '; mode: ' + _html(report['mode']) + '.</p>',
                '<p>Policy: ' + _html(report['policy']['policy_id']) + '<br>SHA-256: ' + _html(report['policy']['canonical_sha256']) + '</p>',
                '<h2>Required and optional checks</h2>', table(('Check', 'Required', 'Outcome', 'Effect', 'Evidence', 'Reasons'), [
                    (x['id'], x['required'], x['outcome'], x['effect'], ', '.join(x['evidence_ids']), ', '.join(x['reason_codes']))
                    for x in report['obligations']]), '<h2>Actions</h2>']
    sections += ['<p>' + _html(x['id']) + ': ' + _html(x.get('message', x.get('title', ''))) + '</p>' for x in report['action_items']]
    if not report['action_items']:
        sections.append('<p>No action items recorded.</p>')
    for engine in report['engines']:
        sections += ['<h2>' + _html(engine['name']) + '</h2>', table(('Status', 'Native verdict', 'Exit', 'Coverage'), [
            (engine['status'], engine['verdict'], engine['exit_code'], engine['coverage']['status'])]),
            table(('Rule', 'Severity', 'Disposition', 'Fingerprint'), [tuple(x[k] for k in (
                'rule', 'severity', 'disposition', 'fingerprint')) for x in engine['findings']]),
            '<pre>' + html.escape(canonical_bytes(engine['coverage']).decode(), quote=True) + '</pre>']
    sections += ['<h2>Limitations</h2><ul>' + ''.join('<li>' + _html(x) + '</li>' for x in report['limitations']) + '</ul>',
                 '<details><summary>Full report, evidence references and raw inventory</summary><pre>' +
                 html.escape(canonical_bytes(report).decode('utf-8'), quote=True) + '</pre></details>']
    return '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Delivery review</title><style>
body{margin:0;background:#f4f6f8;color:#173042;font:16px/1.6 system-ui,sans-serif}main{max-width:1100px;margin:auto;padding:32px}
h1,h2{line-height:1.2}.decision{background:white;padding:16px;border-left:4px solid #617b8d}.table{overflow:auto}
table{border-collapse:collapse;width:100%;background:white;margin:16px 0}td,th{text-align:left;vertical-align:top;border-bottom:1px solid #cbd5dc;padding:10px;overflow-wrap:anywhere}
pre{white-space:pre-wrap;overflow-wrap:anywhere;padding:16px;background:white;font-size:13px}p,li{overflow-wrap:anywhere}summary{cursor:pointer}
</style></head><body><main>''' + ''.join(sections) + '</main></body></html>\n'
