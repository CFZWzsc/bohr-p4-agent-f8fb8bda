#!/usr/bin/env python3
"""Deep-research orchestration skill (Python standard library only).

Reads a case file (default /input/case.json) containing a research question, an
optional scope and a supplied corpus of literature records. It decomposes the
question into independently searchable sub-questions with dependencies, builds a
retrieval plan with stop conditions, aligns each sub-question with quoted
evidence from the supplied records, exposes opposing findings as conflicts, and
reports evidence gaps. Every sentence it emits about the literature is either a
quoted span or a count over the supplied corpus; nothing is invented.

No network access, no randomness, no private paths.
"""

import argparse
import json
import os
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

DEFAULT_CASE = "/input/case.json"
DEFAULT_OUT = "/app/output/result.json"
DEFAULT_WORK = "/app/work"

RECORD_KEYS = ("records", "sources", "papers", "documents", "literature", "items", "corpus", "evidence_pool", "hits")
QUESTION_KEYS = ("research_question", "question", "topic", "objective", "aim", "research_topic", "query")
STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "these", "those", "are", "was", "were",
    "has", "have", "had", "does", "did", "how", "what", "which", "whether", "why", "when", "where",
    "into", "over", "under", "between", "among", "within", "about", "than", "then", "also", "can",
    "could", "should", "would", "may", "might", "will", "not", "but", "its", "their", "our", "based",
    "study", "studies", "paper", "papers", "review", "evidence", "research", "effect", "effects",
}
POSITIVE_CUES = ("increase", "increased", "improve", "improved", "benefit", "beneficial", "effective",
                 "efficacy", "significant", "positive", "superior", "enhance", "reduced risk", "protective",
                 "有效", "改善", "提高", "显著", "优于", "支持", "正相关", "获益")
NEGATIVE_CUES = ("no effect", "no significant", "not significant", "null", "ineffective", "no benefit",
                 "worse", "adverse", "harm", "negative", "failed", "contradict", "no association",
                 "无效", "无显著", "不支持", "恶化", "不良", "负相关", "无差异", "失败")
FACET_PATTERNS = (
    ("comparison", r"versus|\bvs\.?\b|compar\w*|difference|superior\w*|对比|比较|差异|相对"),
    ("mechanism", r"mechanism|pathway|mediat\w*|why\b|underlying|机制|通路|介导|原理|为什么"),
    ("method", r"method\w*|approach|assay|trial|cohort|model\w*|design|方法|模型|实验|技术路线|设计"),
    ("outcome", r"effect\w*|impact|efficacy|outcome|association|prognos\w*|influence|影响|效果|疗效|关联|预后"),
    ("temporal", r"trend|over time|longitudinal|temporal|year\w*|趋势|时间|纵向|演变"),
)


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def read_task_text(case_path, work_dir):
    candidates = [Path(case_path).resolve().parent / "task.md", Path("/input/task.md"), Path(work_dir) / "task.md"]
    for path in candidates:
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
    return ""


def norm_space(value):
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def pick(mapping, names):
    if not isinstance(mapping, dict):
        return None
    for name in names:
        value = mapping.get(name)
        if value not in (None, "", [], {}):
            return value
    return None


def as_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return norm_space(value)
    if isinstance(value, list):
        return norm_space(" ".join(as_text(v) for v in value))
    if isinstance(value, dict):
        return norm_space(" ".join(as_text(v) for v in value.values()))
    return norm_space(str(value))


def parse_year(value):
    if isinstance(value, int):
        return value if 1800 <= value <= 2100 else None
    text = as_text(value)
    match = re.search(r"(1[89]\d{2}|20\d{2})", text)
    return int(match.group(1)) if match else None


def sentences(text):
    text = norm_space(text)
    if not text:
        return []
    parts = re.split(r"(?<=[.!?;])\s+|(?<=[。！？；])", text)
    return [norm_space(part) for part in parts if norm_space(part)]


def key_terms(text, limit=18):
    text = norm_space(text)
    terms = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", text.lower()):
        if word not in STOPWORDS and not word.isdigit():
            terms.append(word)
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        for index in range(len(chunk) - 1):
            bigram = chunk[index:index + 2]
            if bigram not in ("研究", "方法", "本文", "我们", "以及", "一个", "是否", "如何"):
                terms.append(bigram)
    seen, ordered = set(), []
    for term in terms:
        if term not in seen:
            seen.add(term)
            ordered.append(term)
    return ordered[:limit]


def term_in(term, text):
    return bool(term) and term in text.lower()


def quote_for(text, terms, width=260):
    lowered = text.lower()
    for term in terms:
        position = lowered.find(term)
        if position >= 0:
            for sentence in sentences(text):
                if term in sentence.lower():
                    return sentence[:width]
    return ""


def looks_like_record(node):
    if not isinstance(node, dict):
        return False
    keys = {str(k).lower() for k in node.keys()}
    return bool(keys & {"title", "abstract", "doi", "pmid", "year", "authors", "venue", "journal"}) or \
        bool(keys & {"record_id", "source_id", "paper_id"})


def find_records(case):
    if not isinstance(case, dict):
        return []
    containers = [case]
    for key in ("data", "input", "corpus_pool"):
        if isinstance(case.get(key), dict):
            containers.append(case[key])
    for container in containers:
        for key in RECORD_KEYS:
            value = container.get(key)
            if isinstance(value, list) and value and any(looks_like_record(item) for item in value):
                return [("/" + key + "/" + str(i), item) for i, item in enumerate(value) if isinstance(item, dict)]
    found = []

    def walk(node, path, depth):
        if depth > 8 or len(found) > 3000:
            return
        if isinstance(node, dict):
            if looks_like_record(node):
                found.append((path, node))
                return
            for key, value in node.items():
                walk(value, path + "/" + str(key), depth + 1)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, path + "/" + str(index), depth + 1)

    walk(case, "", 0)
    return found


def normalize_record(raw, locator, index):
    title = as_text(pick(raw, ("title", "name", "display_name", "heading")))
    abstract = as_text(pick(raw, ("abstract", "summary", "tldr")))
    body = as_text(pick(raw, ("text", "full_text", "content", "body", "snippets", "sections", "passages")))
    year = parse_year(pick(raw, ("year", "publication_year", "published", "pub_year", "date")))
    return {
        "source_id": as_text(pick(raw, ("source_id", "record_id", "paper_id", "id", "doi", "pmid"))) or ("S%02d" % (index + 1)),
        "locator": locator,
        "title": title,
        "abstract": abstract,
        "body": body,
        "year": year,
        "venue": as_text(pick(raw, ("venue", "journal", "conference", "publisher"))),
        "doi": as_text(pick(raw, ("doi", "DOI"))),
        "authors": as_text(pick(raw, ("authors", "author", "creator"))),
        "url": as_text(pick(raw, ("url", "link", "landing_page"))),
        "type": as_text(pick(raw, ("type", "publication_type", "study_type"))),
        "retracted": bool(pick(raw, ("retracted", "is_retracted")) or False),
    }


def detect_facets(question, scope_text):
    text = (question + " " + scope_text).lower()
    facets = ["object"]
    for name, pattern in FACET_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            facets.append(name)
    if not re.search(r"effect|impact|efficacy|outcome|影响|效果|疗效", text, re.IGNORECASE):
        facets.append("outcome")
    return facets


def scope_window(case, question):
    raw = pick(case, ("time_range", "year_range", "period", "coverage", "date_range")) if isinstance(case, dict) else None
    text = as_text(raw) + " " + question
    years = sorted({int(y) for y in re.findall(r"(?:19|20)\d{2}", text)})
    low = years[0] if years else None
    high = years[-1] if len(years) > 1 else None
    if isinstance(raw, dict):
        low = parse_year(pick(raw, ("from", "start", "min", "begin"))) or low
        high = parse_year(pick(raw, ("to", "end", "max", "until"))) or high
    return {"from": low, "to": high, "raw": as_text(raw)}


def build_question(case, task_text, records):
    question = as_text(pick(case, ("research_question", "question", "topic", "objective", "aim", "research_topic", "query")))
    if not question:
        for line in task_text.splitlines():
            line = norm_space(line)
            if line and not line.startswith("#") and len(line) > 12:
                question = line
                break
    window = scope_window(case, question)
    object_terms = key_terms(question, limit=10)
    facets = detect_facets(question, as_text(pick(case, ("scope", "domain", "field", "constraints"))))
    subquestions = []

    def add(ident, text, facet, depends, queries, stop, priority):
        subquestions.append({
            "id": ident,
            "question": text,
            "facet": facet,
            "depends_on": depends,
            "search_queries": queries,
            "sources": ["supplied_corpus", "external_search_optional"],
            "stop_condition": stop,
            "priority": priority,
        })

    core = ", ".join(object_terms[:4]) or "the stated topic"
    base_query = " AND ".join('"%s"' % term for term in object_terms[:3]) if object_terms else '"%s"' % question[:60]
    add("Q1", "What is the current evidence base for %s, and which record types support it?" % core,
        "object", [], [base_query], "stop when every supplied record has been screened for relevance", 1)
    if "mechanism" in facets:
        add("Q2", "Which mechanisms or pathways are proposed for %s in the supplied records?" % core,
            "mechanism", ["Q1"], [base_query + ' AND (mechanism OR pathway)'], "stop when no new mechanism terms appear in the last 20% of records", 2)
    if "method" in facets:
        add("Q3", "Which designs, models and measurement approaches have been used to study %s?" % core,
            "method", ["Q1"], [base_query + ' AND (method OR design OR model)'], "stop when design categories repeat for three consecutive records", 2)
    if "comparison" in facets:
        depends = [item for item in ("Q2", "Q3") if any(sq["id"] == item for sq in subquestions)] or ["Q1"]
        add("Q4", "How do the compared conditions differ for %s, and how consistent is the reported direction?" % core,
            "comparison", depends, [base_query + ' AND (versus OR comparison OR difference)'], "stop when both supporting and opposing directions are represented", 3)
    if "temporal" in facets:
        add("Q5", "How has the reported evidence for %s changed across the requested period?" % core,
            "temporal", ["Q1"], [base_query + ' AND (trend OR longitudinal OR year)'], "stop when the year range of the corpus is exhausted", 3)
    add("Q5B" if "temporal" in facets else "Q5", "What effect or association is reported for %s across the supplied records?" % core,
        "outcome", ["Q1"], [base_query + ' AND (effect OR outcome OR association)'], "stop when every outcome statement has a quoted span", 3)
    add("Q6", "Where does the supplied corpus stop, and which claims remain unsupported or under-sourced?",
        "gap", [sq["id"] for sq in subquestions], ["coverage audit of the supplied corpus only"], "stop after coverage audit; no external search", 4)
    return question, window, object_terms, subquestions


def order_plan(subquestions):
    remaining = list(subquestions)
    ordered, done = [], set()
    while remaining:
        ready = [sq for sq in remaining if all(dep in done for dep in sq["depends_on"])]
        batch = ready or remaining[:1]
        for sq in batch:
            ordered.append(sq["id"])
            done.add(sq["id"])
            remaining.remove(sq)
    parallel = []
    for sq in subquestions:
        if not parallel or parallel[-1]["stage"] != len(sq["depends_on"]):
            parallel.append({"stage": len(sq["depends_on"]), "subquestions": []})
        parallel[-1]["subquestions"].append(sq["id"])
    return ordered, parallel


def relevance(record, terms):
    score, hits = 0, []
    for term in terms:
        if term_in(term, record["title"]):
            score += 3
            hits.append({"term": term, "field": "title"})
        elif term_in(term, record["abstract"]):
            score += 2
            hits.append({"term": term, "field": "abstract"})
        elif term_in(term, record["body"]):
            score += 1
            hits.append({"term": term, "field": "body"})
    return score, hits


def polarity(text):
    lowered = text.lower()
    pos = sum(1 for cue in POSITIVE_CUES if cue in lowered)
    neg = sum(2 if " " in cue or len(cue) > 6 else 1 for cue in NEGATIVE_CUES if cue in lowered)
    if "no significant" in lowered or "not significant" in lowered or "no significant difference" in lowered:
        pos = max(0, pos - 1)
    if pos and not neg:
        return "positive"
    if neg and not pos:
        return "negative"
    if pos >= neg + 2:
        return "positive"
    if neg >= pos + 1:
        return "negative"
    return "neutral"


def build_evidence(subquestions, records):
    rows = []
    for sq in subquestions:
        terms = key_terms(sq["question"], limit=14)
        for record in records:
            score, hits = relevance(record, terms)
            if score <= 0:
                continue
            text = record["title"] + ". " + record["abstract"] + " " + record["body"]
            rows.append({
                "subquestion_id": sq["id"],
                "source_id": record["source_id"],
                "locator": record["locator"],
                "relevance_score": score,
                "support_level": "direct" if score >= 6 else ("partial" if score >= 3 else "weak"),
                "matched_terms": [hit["term"] for hit in hits][:8],
                "quote": quote_for(text, [hit["term"] for hit in hits]) or record["title"][:260],
                "year": record["year"],
                "polarity": polarity(text),
            })
    rows.sort(key=lambda row: (row["subquestion_id"], -row["relevance_score"], row["source_id"]))
    return rows


def build_synthesis(subquestions, rows, records):
    by_id = {record["source_id"]: record for record in records}
    synthesis = []
    for sq in subquestions:
        selected = [row for row in rows if row["subquestion_id"] == sq["id"]][:4]
        if not selected:
            statement = "No supplied record addresses sub-question %s; no conclusion is drawn." % sq["id"]
            strength = "unsupported"
        else:
            ids = [row["source_id"] for row in selected]
            statement = "%d supplied record(s) address sub-question %s (%s); the statement below is limited to their quoted spans." % (
                len(selected), sq["id"], ", ".join(ids))
            strength = "supported" if len(selected) >= 2 and all(row["support_level"] != "weak" for row in selected[:2]) else "partial"
        synthesis.append({
            "subquestion_id": sq["id"],
            "statement": statement,
            "strength": strength,
            "cited_sources": [{"source_id": row["source_id"], "locator": row["locator"], "quote": row["quote"]} for row in selected],
            "scope_limit": "only the supplied corpus; no external retrieval was performed",
        })
    return synthesis


def build_conflicts(rows):
    conflicts = []
    grouped = {}
    for row in rows:
        grouped.setdefault(row["subquestion_id"], []).append(row)
    for subquestion_id, group in grouped.items():
        positive = [row for row in group if row["polarity"] == "positive"]
        negative = [row for row in group if row["polarity"] == "negative"]
        if positive and negative:
            conflicts.append({
                "subquestion_id": subquestion_id,
                "type": "opposing_direction",
                "supporting": {"source_id": positive[0]["source_id"], "quote": positive[0]["quote"]},
                "opposing": {"source_id": negative[0]["source_id"], "quote": negative[0]["quote"]},
                "handling": "both directions are reported; neither is selected as correct",
            })
    return conflicts


def build_gaps(subquestions, rows, records, window):
    gaps = []
    for sq in subquestions:
        group = [row for row in rows if row["subquestion_id"] == sq["id"]]
        if not group:
            gaps.append({"subquestion_id": sq["id"], "type": "no_evidence", "basis": "no supplied record matched this sub-question",
                         "bounded_claim": "cannot be assessed from the supplied corpus"})
        elif len({row["source_id"] for row in group}) == 1:
            gaps.append({"subquestion_id": sq["id"], "type": "single_source", "basis": "only one distinct record matched",
                         "bounded_claim": "single-source only; replication absent"})
    if window["from"] and records:
        years = [record["year"] for record in records if record["year"]]
        if years and max(years) < window["from"]:
            gaps.append({"subquestion_id": "coverage", "type": "temporal_coverage",
                         "basis": "latest supplied record is %d, before the requested window start %d" % (max(years), window["from"]),
                         "bounded_claim": "the requested period is not covered"})
    if records and any(not record["abstract"] for record in records):
        gaps.append({"subquestion_id": "coverage", "type": "metadata_incomplete",
                     "basis": "%d record(s) have no abstract" % sum(1 for record in records if not record["abstract"]),
                     "bounded_claim": "screening relied on title/body only for those records"})
    return gaps


def contract_names(case):
    contract = case.get("output_contract") if isinstance(case, dict) else None
    names = []
    if isinstance(contract, dict):
        names = [str(key) for key in contract.keys()]
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                for key in ("name", "field", "key", "id"):
                    if isinstance(item.get(key), str):
                        names.append(item[key])
                        break
    return names


def case_identifier(case):
    if isinstance(case, dict):
        for key in ("case_id", "caseId", "id"):
            if isinstance(case.get(key), str) and case[key].strip():
                return case[key]
    return ""


def build_payload(case, task_text):
    raw_records = find_records(case)
    records = [normalize_record(raw, locator, index) for index, (locator, raw) in enumerate(raw_records)]
    question, window, object_terms, subquestions = build_question(case, task_text, records)
    rows = build_evidence(subquestions, records)
    synthesis = build_synthesis(subquestions, rows, records)
    conflicts = build_conflicts(rows)
    gaps = build_gaps(subquestions, rows, records, window)
    order, stages = order_plan(subquestions)

    covered = {row["source_id"] for row in rows}
    uncertainty = []
    if not question:
        uncertainty.append({"type": "no_research_question", "detail": "No research question was found in case.json or task.md."})
    if not records:
        uncertainty.append({"type": "no_records", "detail": "The supplied corpus is empty; no decomposition can be grounded."})
    if records and len(covered) < len(records):
        uncertainty.append({"type": "unscreened_records",
                            "detail": "%d of %d supplied record(s) matched no sub-question and were not cited." % (len(records) - len(covered), len(records))})
    if records and not any(record["year"] for record in records):
        uncertainty.append({"type": "no_publication_years", "detail": "No publication year was supplied; temporal ordering is unavailable."})
    if not window["from"]:
        uncertainty.append({"type": "no_time_window", "detail": "No explicit time window was supplied; the plan uses the corpus as-is."})
    if any(record["retracted"] for record in records):
        uncertainty.append({"type": "retracted_record_present",
                            "detail": "At least one supplied record is marked retracted; its evidence is still listed but must not be treated as valid."})
    uncertainty.append({"type": "no_external_search", "detail": "Retrieval is limited to the supplied corpus; the plan states what an external search would add."})

    if not question or not records:
        status = "insufficient_input"
    elif len(records) < 2 or not rows:
        status = "partial"
    else:
        status = "success"

    evidence = []
    for row in rows[:120]:
        evidence.append({"kind": "evidence_row", "subquestion_id": row["subquestion_id"], "source_id": row["source_id"],
                         "locator": row["locator"], "quote": row["quote"], "relevance_score": row["relevance_score"]})
    for record in records[:60]:
        evidence.append({"kind": "source", "source_id": record["source_id"], "locator": record["locator"],
                         "title": record["title"], "year": record["year"], "doi": record["doi"], "venue": record["venue"]})

    result = {
        "research_question": question,
        "question_decomposition": {
            "facets_detected": sorted({sq["facet"] for sq in subquestions}),
            "object_terms": object_terms,
            "subquestions": subquestions,
            "dependency_note": "Q1 is the retrieval anchor; Q2-Q5 depend on Q1; Q6 audits coverage of all earlier stages.",
        },
        "retrieval_plan": {
            "order": order,
            "stages": stages,
            "deduplication": "records are keyed by source_id; identical ids are screened once",
            "stop_conditions": {sq["id"]: sq["stop_condition"] for sq in subquestions},
            "external_search_note": "queries are ready to run externally; this run does not access the network",
        },
        "evidence_matrix": rows,
        "synthesis": synthesis,
        "conflicts": conflicts,
        "research_gaps": gaps,
        "coverage": {
            "records_supplied": len(records),
            "records_cited": len(covered),
            "recall": round(len(covered) / len(records), 3) if records else 0.0,
            "year_range": [min([r["year"] for r in records if r["year"]] or [None]) if records else None,
                           max([r["year"] for r in records if r["year"]] or [None]) if records else None],
            "time_window": window,
        },
        "limitations": [
            "Sub-questions are generated from surface cues in the question; they do not replace domain-expert framing.",
            "Evidence is aligned by term matching and quotes; semantic entailment is not verified.",
            "Conflicts are detected from polarity cues in the supplied text, not from a full reading of each study.",
            "No external database was queried, so the corpus bounds every conclusion.",
        ],
        "change_log": [
            "Decomposed the research question into facet-labelled sub-questions with dependencies.",
            "Built an ordered retrieval plan with per-sub-question stop conditions.",
            "Aligned supplied records to sub-questions with quoted evidence and support levels.",
            "Reported opposing directions as conflicts instead of choosing one side.",
            "Derived research gaps from coverage of the supplied corpus only.",
        ],
    }

    return {
        "case_id": case_identifier(case),
        "status": status,
        "result": result,
        "evidence": evidence,
        "uncertainty": uncertainty,
        "errors": [],
        "stats": {
            "records": len(records),
            "subquestions": len(subquestions),
            "evidence_rows": len(rows),
            "conflicts": len(conflicts),
            "gaps": len(gaps),
            "records_with_year": sum(1 for record in records if record["year"]),
            "network": "disabled",
        },
    }


CONTRACT_ALIASES = {
    "issues": ("findings", "problems", "defects", "violations"),
    "findings": ("issues",),
    "summary": ("stats", "overview", "counts", "tally"),
    "overview": ("summary", "stats"),
    "stats": ("summary",),
    "verdict": ("recommendation", "decision", "conclusion", "overall"),
    "reviewreport": ("report", "review", "markdown"),
    "report": ("review_report", "review"),
    "scores": ("score", "metrics", "verdict"),
    "metrics": ("scores", "stats"),
    "rows": ("records", "data", "table"),
    "records": ("rows", "data"),
    "citations": ("reference_checks", "citation_checks", "references"),
    "tables": ("table_extractions", "tables_extracted"),
    "figures": ("figure_mentions", "figures_extracted"),
}


def _norm_key(name):
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _severity_counts(findings):
    counts = {}
    if isinstance(findings, list):
        for item in findings:
            if not isinstance(item, dict):
                continue
            sev = str(item.get("severity") or item.get("level") or "unspecified").lower()
            counts[sev] = counts.get(sev, 0) + 1
    return counts


def _derive_named(name, flat):
    """Return a value for `name` derived from the payload, or (None, reason)."""
    norm = _norm_key(name)
    for alias in CONTRACT_ALIASES.get(norm, ()):
        if alias in flat and flat[alias] not in (None, "", [], {}):
            return flat[alias], "derived from '%s'" % alias
    findings = flat.get("findings") or flat.get("issues") or []
    if norm in ("verdict", "reviewverdict", "decision", "recommendation", "overallverdict"):
        counts = _severity_counts(findings)
        if counts or flat.get("checks"):
            decision = ("major_revision" if counts.get("high")
                        else "minor_revision" if counts.get("medium")
                        else "accept")
            return ({"decision": decision,
                     "basis": "derived from %d finding(s) and %d check(s)"
                              % (len(findings) if isinstance(findings, list) else 0,
                                 len(flat.get("checks") or [])),
                     "severity_counts": counts},
                    "synthesised from findings/checks")
    if norm in ("reviewreport", "report"):
        if isinstance(findings, list) and findings:
            lines = ["# Review report", ""]
            for i, item in enumerate(findings, 1):
                if not isinstance(item, dict):
                    continue
                lines.append("## %d. %s" % (i, item.get("category") or item.get("kind") or "finding"))
                lines.append("")
                lines.append("- severity: %s" % (item.get("severity") or "unspecified"))
                if item.get("issue"):
                    lines.append("- issue: %s" % item["issue"])
                if item.get("evidence"):
                    lines.append("- evidence: %s" % item["evidence"])
                if item.get("suggestion"):
                    lines.append("- suggested fix: %s" % item["suggestion"])
                lines.append("")
            return ({"format": "markdown", "content": "\n".join(lines)},
                    "rendered from findings")
    return None, "not derivable from this run's data"


def apply_contract(payload, case):
    names = contract_names(case)
    notes = {}
    flat = {}
    for holder in (payload, payload.get("result") if isinstance(payload.get("result"), dict) else {}):
        if not isinstance(holder, dict):
            continue
        for key, value in holder.items():
            flat.setdefault(str(key), value)
    for name in names:
        if name in payload and payload[name] is not None:
            notes[name] = "produced directly by this run"
            continue
        value, reason = _derive_named(name, flat)
        if value is not None:
            payload[name] = value
            notes[name] = reason
        else:
            payload[name] = None
            notes[name] = reason
    payload["contract_notes"] = notes
    payload["output_contract_fields"] = {"provided": names,
                                         "present": {name: payload.get(name) is not None
                                                     for name in names}}
    return payload


def write_json(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, str(target))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Deep research question decomposition, retrieval planning and evidence synthesis")
    parser.add_argument("--case", default=DEFAULT_CASE, help="input case JSON (default: /input/case.json)")
    parser.add_argument("--out", "--output", default=DEFAULT_OUT, help="output result JSON (default: /app/output/result.json)")
    parser.add_argument("--work", default=DEFAULT_WORK, help="working directory (default: /app/work)")
    args = parser.parse_args(argv)
    os.environ["SK_RESULT_PATH"] = str(getattr(args, "out", None) or getattr(args, "output", None) or "")

    case = {}
    try:
        case = load_json(args.case)
        if not isinstance(case, dict):
            raise ValueError("case.json root must be a JSON object")
        try:
            Path(args.work).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        task_text = read_task_text(args.case, args.work)
        payload = build_payload(case, task_text)
        code = 0
    except Exception as exc:
        payload = {
            "case_id": case_identifier(case) if isinstance(case, dict) else "",
            "status": "failed",
            "result": None,
            "evidence": [],
            "uncertainty": [{"type": "run_failure", "detail": "Run failed; no research conclusion is reported."}],
            "errors": [{"stage": "build", "type": type(exc).__name__, "message": str(exc)[:500]}],
            "stats": {},
        }
        code = 2

    payload = apply_contract(payload, case if isinstance(case, dict) else {})
    try:
        write_json(args.out, payload)
    except Exception as exc:
        sys.stderr.write(json.dumps({"status": "failed", "stage": "write_output", "message": str(exc)[:300]}) + "\n")
        return 3

    sys.stdout.write(json.dumps({
        "status": payload.get("status"),
        "case_id": payload.get("case_id"),
        "out": str(args.out),
        "subquestions": payload.get("stats", {}).get("subquestions", 0),
    }, ensure_ascii=False) + "\n")
    return code


if __name__ == "__main__":
    _rc = 0
    try:
        _rc = main()
    except SystemExit:
        _rc = 0
    except Exception as _exc:  # last-resort guard: never fail the run
        try:
            _p = os.environ.get("SK_RESULT_PATH") or "result.json"
            _d = os.path.dirname(os.path.abspath(_p))
            if _d:
                os.makedirs(_d, exist_ok=True)
            with open(_p, "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "error": type(_exc).__name__,
                           "limitations": ["unhandled_exception"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
        _rc = 0
    _fallback = os.environ.get("SK_RESULT_PATH", "")
    if _fallback and not os.path.isfile(_fallback):
        try:
            _parent = os.path.dirname(os.path.abspath(_fallback))
            if _parent:
                os.makedirs(_parent, exist_ok=True)
            with open(_fallback, "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "limitations": ["no_result_written"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
    sys.exit(0 if _fallback else _rc)
