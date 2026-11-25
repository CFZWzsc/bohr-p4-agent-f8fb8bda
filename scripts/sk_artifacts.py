#!/usr/bin/env python3
"""Companion artifact writer for the deep-research skill (Python standard library only).

Given the payload produced by run_skill.py, writes six human-reviewable files next
to result.json and returns the registration list stored in result["artifacts"]:

  research_report.md   human-readable synthesis report
  synthesis_matrix.csv one row per sub-question x source evidence binding
  screening_log.csv    one row per supplied record with the screening decision
  prisma_flow.svg      PRISMA-style count flow for the supplied corpus
  provenance.json      sha256 digests for records, quotes and the case id
  validation.json      the verification / self_check / gates / validator_verdict block

No timestamps are written, so repeated runs over the same input are byte-identical.
"""

import csv
import hashlib
import io
import json
import os


def _csv_text(header, rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(["" if value is None else value for value in row])
    return buffer.getvalue()


def _join(value):
    if isinstance(value, (list, tuple)):
        return "; ".join(str(item) for item in value)
    return "" if value is None else str(value)


def _quote(value):
    return ("" if value is None else str(value)).replace("\r", " ").replace("\n", " ")


def render_synthesis_csv(payload):
    rows = (payload.get("result") or {}).get("evidence_matrix") or []
    header = ["subquestion_id", "source_id", "locator", "relevance_score", "coverage_fraction",
              "support_level", "polarity", "quote_field", "quote", "year"]
    body = [[row.get("subquestion_id"), row.get("source_id"), row.get("locator"), row.get("relevance_score"),
             row.get("coverage_fraction"), row.get("support_level"), row.get("polarity"), row.get("quote_field"),
             _quote(row.get("quote")), row.get("year")] for row in rows]
    return _csv_text(header, body)


def render_screening_csv(payload):
    rows = payload.get("screening") or []
    header = ["screening_id", "source_id", "locator", "title", "year", "doi", "decision", "reason",
              "criteria_applied", "duplicate_of", "matched_subquestions"]
    body = [[row.get("screening_id"), row.get("source_id"), row.get("locator"), _quote(row.get("title")),
             row.get("year"), row.get("doi"), row.get("decision"), _quote(row.get("reason")),
             _join(row.get("criteria_applied")), row.get("duplicate_of"),
             _join(row.get("matched_subquestions"))] for row in rows]
    return _csv_text(header, body)


def render_prisma_svg(payload):
    prisma = payload.get("prisma") or {}
    stages = [
        ("Identified", prisma.get("n_identified", 0)),
        ("Duplicates removed", prisma.get("n_duplicates", 0)),
        ("Screened", prisma.get("n_screened", 0)),
        ("Excluded by code", prisma.get("n_excluded_by_code", 0)),
        ("Included", prisma.get("n_included", 0)),
        ("Pending", prisma.get("n_pending", 0)),
    ]
    width, height, box_w, box_h = 760, 420, 190, 62
    positions = [(30, 40), (280, 40), (530, 40), (30, 180), (280, 180), (530, 180)]
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
        'role="img" aria-label="PRISMA flow for the supplied corpus">' % (width, height, width, height),
        '  <rect width="100%" height="100%" fill="#ffffff"/>',
        '  <text x="30" y="28" font-family="Helvetica, Arial, sans-serif" font-size="18" fill="#1f2937">'
        'PRISMA flow (supplied corpus)</text>',
    ]
    for (label, count), (x, y) in zip(stages, positions):
        parts.append('  <rect x="%d" y="%d" width="%d" height="%d" rx="6" fill="#eef2ff" stroke="#4338ca"/>'
                     % (x, y, box_w, box_h))
        parts.append('  <text x="%d" y="%d" font-family="Helvetica, Arial, sans-serif" font-size="13" '
                     'fill="#312e81">%s</text>' % (x + 12, y + 24, label))
        parts.append('  <text x="%d" y="%d" font-family="Helvetica, Arial, sans-serif" font-size="22" '
                     'font-weight="bold" fill="#111827">%d</text>' % (x + 12, y + 50, int(count or 0)))
    arrows = [((220, 71), (280, 71)), ((470, 71), (530, 71)), ((625, 102), (625, 180)),
              ((220, 211), (280, 211)), ((470, 211), (530, 211))]
    for (x1, y1), (x2, y2) in arrows:
        parts.append('  <line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#6b7280" stroke-width="2" '
                     'marker-end="url(#arrow)"/>' % (x1, y1, x2, y2))
    parts.insert(3, '  <defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" '
                    'orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#6b7280"/></marker></defs>')
    parts.append('</svg>')
    return "\n".join(parts) + "\n"


def render_report(payload):
    result = payload.get("result") or {}
    prisma = payload.get("prisma") or {}
    decomposition = result.get("question_decomposition") or {}
    plan = result.get("retrieval_plan") or {}
    lines = [
        "# Deep research report",
        "",
        "- Case: `%s`" % (payload.get("case_id") or "unknown"),
        "- Status: `%s`" % (payload.get("status") or "unknown"),
        "- Network: disabled; conclusions are bounded by the supplied corpus.",
        "",
        "## Research question",
        "",
        result.get("research_question") or "_No research question was supplied._",
        "",
        "## Declared retrieval plan",
        "",
        "- State: `%s`" % (plan.get("state") or "declared"),
        "- Execution order: %s" % ", ".join(plan.get("order") or []) or "- Execution order: —",
        "",
        "| Sub-question | Facet | Query | Stop condition |",
        "|---|---|---|---|",
    ]
    for sq in decomposition.get("subquestions") or []:
        lines.append("| %s | %s | %s | %s |" % (
            sq.get("id"), sq.get("facet"), _quote("; ".join(sq.get("search_queries") or [])),
            _quote(sq.get("stop_condition"))))
    lines += [
        "",
        "## Screening (PRISMA)",
        "",
        "- Identified: %s" % prisma.get("n_identified", 0),
        "- Duplicates removed: %s" % prisma.get("n_duplicates", 0),
        "- Screened: %s" % prisma.get("n_screened", 0),
        "- Excluded by code: %s" % prisma.get("n_excluded_by_code", 0),
        "- Included: %s" % prisma.get("n_included", 0),
        "- Pending: %s" % prisma.get("n_pending", 0),
        "- Count balance ok: %s" % bool(prisma.get("balance_ok")),
        "",
        "## Evidence synthesis",
        "",
    ]
    if not result.get("synthesis"):
        lines.append("_No sub-question had a citable evidence row._")
        lines.append("")
    for item in result.get("synthesis") or []:
        lines.append("### %s" % item.get("subquestion_id"))
        lines.append("")
        lines.append("- Direction: `%s`; strength: `%s`" % (item.get("direction"), item.get("strength")))
        lines.append("- Statement: %s" % _quote(item.get("statement")))
        for cited in item.get("cited_sources") or []:
            lines.append("- `%s` (%s): \"%s\"" % (cited.get("source_id"), cited.get("locator"),
                                                  _quote(cited.get("quote"))))
        lines.append("")
    lines += ["## Conflicts", ""]
    if not result.get("conflicts"):
        lines.append("_No opposing direction pair was detected in the supplied corpus._")
        lines.append("")
    for index, conflict in enumerate(result.get("conflicts") or [], 1):
        lines.append("### Conflict %d" % index)
        lines.append("")
        for side in ("supporting", "opposing"):
            entry = conflict.get(side) or {}
            lines.append("- %s `%s` (%s): \"%s\"" % (side, entry.get("source_id"), entry.get("locator"),
                                                     _quote(entry.get("quote"))))
        lines.append("- Sources distinct: %s" % bool(conflict.get("source_distinct")))
        lines.append("")
    lines += ["## Research gaps", ""]
    if not result.get("research_gaps"):
        lines.append("_No coverage gap was derived from the supplied corpus._")
        lines.append("")
    for gap in result.get("research_gaps") or []:
        lines.append("### %s — %s" % (gap.get("subquestion_id"), gap.get("type")))
        lines.append("")
        lines.append("- Basis: %s" % _quote(gap.get("basis")))
        lines.append("- Distinction: %s" % _quote(gap.get("distinction")))
        lines.append("- Queries tried: %s" % _quote(_join([query.get("query") for query in
                                                            gap.get("queries_tried") or []])))
        lines.append("- Human confirmation: %s" % _quote(gap.get("human_confirm")))
        lines.append("")
    lines += ["## Limitations", ""]
    for item in result.get("limitations") or []:
        lines.append("- %s" % item)
    lines += [
        "",
        "## Artifacts",
        "",
        "- research_report.md",
        "- synthesis_matrix.csv",
        "- screening_log.csv",
        "- prisma_flow.svg",
        "- provenance.json",
        "- validation.json",
    ]
    return "\n".join(lines) + "\n"


def render_provenance(payload):
    result = payload.get("result") or {}
    rows_by_source = {}
    for row in result.get("evidence_matrix") or []:
        rows_by_source.setdefault(row.get("source_id"), []).append(row)
    sources = []
    for item in payload.get("screening") or []:
        source_id = item.get("source_id")
        quotes = []
        for row in rows_by_source.get(source_id, []):
            quotes.append({
                "subquestion_id": row.get("subquestion_id"),
                "quote_sha256": hashlib.sha256((row.get("quote") or "").encode("utf-8")).hexdigest(),
            })
        sources.append({
            "source_id": source_id,
            "locator": item.get("locator"),
            "title_sha256": hashlib.sha256((item.get("title") or "").encode("utf-8")).hexdigest(),
            "doi_sha256": hashlib.sha256((item.get("doi") or "").encode("utf-8")).hexdigest(),
            "year": item.get("year"),
            "screening_decision": item.get("decision"),
            "quotes": quotes,
        })
    return {
        "schema": "provenance/v1",
        "case_id": payload.get("case_id") or "",
        "algorithm": "sha256",
        "sources": sources,
        "artifacts": ["research_report.md", "synthesis_matrix.csv", "screening_log.csv",
                      "prisma_flow.svg", "provenance.json", "validation.json"],
    }


def render_validation(payload):
    return {
        "schema": "validation/v1",
        "case_id": payload.get("case_id") or "",
        "status": payload.get("status") or "",
        "verification": payload.get("verification") or {},
        "self_check": payload.get("self_check") or [],
        "gates": payload.get("gates") or [],
        "validator_verdict": payload.get("validator_verdict") or {},
    }


def write_artifacts(payload, out_dir):
    """Write the companion files into out_dir and return the result["artifacts"] registry."""
    builders = [
        ("research_report.md", "report", render_report(payload)),
        ("synthesis_matrix.csv", "table", render_synthesis_csv(payload)),
        ("screening_log.csv", "table", render_screening_csv(payload)),
        ("prisma_flow.svg", "figure", render_prisma_svg(payload)),
        ("provenance.json", "provenance", json.dumps(render_provenance(payload), ensure_ascii=False, indent=2) + "\n"),
        ("validation.json", "validation", json.dumps(render_validation(payload), ensure_ascii=False, indent=2) + "\n"),
    ]
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    registry = []
    for name, kind, content in builders:
        path = os.path.join(out_dir, name)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        encoded = content.encode("utf-8")
        registry.append({
            "name": name,
            "kind": kind,
            "path": name,
            "exists": os.path.exists(path),
            "bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        })
    return registry
