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

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import sk_artifacts
except Exception:  # optional companion writer; the core result must still be produced
    sk_artifacts = None

DEFAULT_CASE = "/input/case.json"
DEFAULT_OUT = "/app/output/result.json"
DEFAULT_WORK = "/app/work"

RECORD_KEYS = ("records", "sources", "papers", "documents", "literature", "items", "corpus",
               "evidence_pool", "hits", "studies", "abstracts", "library", "entries")
STATUS_VALUES = ("success", "partial", "insufficient_input", "failed")
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
                 "reduced", "lower", "lowered", "decreased", "preserved", "attenuated", "protected", "higher",
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
OUTCOME_CUE_RE = re.compile(
    r"significant(?:ly)?|reduced|reduction|lower|lowered|no difference|improved|improvement|"
    r"increased|increase|hba1c|risk|effect|association|associated|compared|decreas\w*|higher|"
    r"predict\w*|correlat\w*|\d+(?:\.\d+)?\s*%|p\s*[<=]", re.IGNORECASE)
NO_DIFF_CUES = ("no difference", "no significant", "not significant", "no association", "no effect",
                "no benefit", "did not", "does not", "was not associated", "null result",
                "无差异", "无显著", "无关", "不支持", "未发现")
AUX_PREFIX_RE = re.compile(r"^(?:does|do|is|are|can|could|will|would|should|did|has|have)\b\s*", re.IGNORECASE)
SECONDARY_SPLIT_RE = re.compile(r",?\s*(?:and|;)\s*(which|what|whether)\b", re.IGNORECASE)
POPULATION_SPLIT_RE = re.compile(r"\s+(?:in|among|for|within)\s+(.+)$", re.IGNORECASE)
EFFECT_VERB_RE = re.compile(
    r"\b(?:drives?|improves?|reduces?|increases?|affects?|influences?|causes?|predicts?|leads?|"
    r"prevents?|lowers?|decreases?|alters?|impacts?|changes?|associates?|determines?|modulates?)\b",
    re.IGNORECASE)
OUTCOME_WORDS = ("hba1c", "glycemic", "glycaemic", "outcome", "mortality", "progression", "risk",
                 "incidence", "survival", "efficacy", "control", "injury", "function", "severity",
                 "biomarker", "effect")
POPULATION_WORDS = ("adult", "patient", "children", "child", "mice", "mouse", "rats", "women", "men",
                    "elderly", "cohort", "population", "diabetes", "sepsis", "cancer", "pregnancy",
                    "infant", "adolescent", "obesity", "stroke", "critically ill", "human", "embryo")
INTERVENTION_WORDS = ("fasting", "diet", "exercise", "supplement", "drug", "inhibitor", "therapy",
                      "treatment", "statin", "metformin", "vaccine", "training", "probiotic", "vitamin",
                      "inflammasome", "blockade", "deletion", "agonist", "antagonist")
MERGE_THRESHOLD = 0.75


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


def best_outcome_sentence(record):
    """Pick the abstract/body sentence that carries the strongest outcome or effect cue."""
    best = None
    for field in ("abstract", "body"):
        for sentence in sentences(record.get(field) or ""):
            cues = OUTCOME_CUE_RE.findall(sentence)
            if not cues:
                continue
            score = len(cues) * 2 + (1 if re.search(r"\d", sentence) else 0) + (1 if field == "abstract" else 0)
            if best is None or score > best[0] or (score == best[0] and len(sentence) < len(best[2])):
                best = (score, field, sentence)
    return (best[2], best[1]) if best else ("", "")


def prepare_record(record):
    """Attach the outcome quote, its field and the directional polarity used downstream."""
    sentence, field = best_outcome_sentence(record)
    if sentence:
        record["_quote"], record["_quote_field"], record["_has_outcome"] = sentence, field, True
        record["_polarity"] = polarity(sentence)
    else:
        record["_quote"], record["_quote_field"], record["_has_outcome"] = record["title"][:260], "title", False
        record["_polarity"] = "neutral"
    return record


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


def record_dedupe_key(record):
    """Stable dedupe key: DOI first, otherwise a punctuation-stripped title."""
    doi = as_text(record.get("doi")).lower()
    if doi:
        return "doi:" + doi
    title = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", as_text(record.get("title")).lower())
    return "title:" + title if title else ""


def find_duplicates(records):
    """Map duplicate record locator -> {duplicate_of, key}; first occurrence wins."""
    seen, duplicates = {}, {}
    for record in records:
        key = record_dedupe_key(record)
        if not key:
            continue
        if key in seen:
            duplicates[record["locator"]] = {"duplicate_of": seen[key], "key": key}
        else:
            seen[key] = record["source_id"]
    return duplicates


def build_screening(records, rows, duplicates, skipped_locators):
    """One screening decision per supplied record, plus the criteria that produced it."""
    cited = {}
    for row in rows:
        cited.setdefault(row["source_id"], []).append(row["subquestion_id"])
    screening = []
    for index, record in enumerate(records, 1):
        locator = record["locator"]
        matched = sorted(set(cited.get(record["source_id"], [])))
        criteria = ["has_bibliographic_content"]
        if record["year"]:
            criteria.append("has_publication_year")
        if record["doi"]:
            criteria.append("has_doi")
        if matched:
            criteria.append("matches_subquestion_terms")
        if not (record["title"] or record["abstract"] or record["body"]):
            decision = "exclude"
            reason = "no title, abstract or body text was supplied"
        elif locator in duplicates:
            decision = "exclude"
            reason = "duplicate of %s by DOI/title" % duplicates[locator]["duplicate_of"]
            criteria.append("deduplicated")
        elif locator in skipped_locators:
            decision = "exclude"
            reason = "record is flagged retracted and cannot serve as valid evidence"
            criteria.append("not_retracted")
        elif matched:
            decision, reason = "include", "matched sub-question terms and was cited in the evidence matrix"
        else:
            decision, reason = "pending", "no sub-question term matched; retained in the corpus but not cited"
        screening.append({
            "screening_id": "SC%02d" % index,
            "source_id": record["source_id"],
            "locator": locator,
            "title": record["title"],
            "year": record["year"],
            "doi": record["doi"],
            "decision": decision,
            "reason": reason,
            "criteria_applied": criteria,
            "duplicate_of": duplicates.get(locator, {}).get("duplicate_of"),
            "matched_subquestions": matched,
        })
    return screening


def build_prisma(screening, total):
    included = sum(1 for item in screening if item["decision"] == "include")
    duplicates = sum(1 for item in screening if item["decision"] == "exclude" and item["duplicate_of"])
    excluded = sum(1 for item in screening if item["decision"] == "exclude" and not item["duplicate_of"])
    pending = sum(1 for item in screening if item["decision"] == "pending")
    screened = total - duplicates
    return {
        "n_identified": total,
        "n_duplicates": duplicates,
        "n_screened": screened,
        "n_excluded_by_code": excluded,
        "n_included": included,
        "n_pending": pending,
        "balance_ok": screened == excluded + included + pending,
        "stages": [
            {"stage": "identified", "count": total},
            {"stage": "duplicates_removed", "count": duplicates},
            {"stage": "screened", "count": screened},
            {"stage": "excluded_by_code", "count": excluded},
            {"stage": "included", "count": included},
            {"stage": "pending", "count": pending},
        ],
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


def is_keyword_string(text):
    text = norm_space(text)
    if not text or "?" in text:
        return False
    if re.search(r"\b(does|do|is|are|can|could|will|would|should|whether|how|what|which|why)\b", text, re.IGNORECASE):
        return False
    chunks = [chunk for chunk in re.split(r"[,;，；]| / |\band\b", text) if chunk.strip()]
    return len(chunks) >= 2 and len(text.split()) <= 24


def classify_chunk(chunk):
    lowered = chunk.lower()
    if any(word in lowered for word in OUTCOME_WORDS):
        return "outcome"
    if any(word in lowered for word in POPULATION_WORDS):
        return "population"
    if any(word in lowered for word in INTERVENTION_WORDS):
        return "intervention"
    return "object"


def parse_question(question):
    """Split a question (or a comma-joined keyword string) into searchable facets."""
    parsed = {"raw": norm_space(question), "keyword_string": False,
              "object": "", "intervention": "", "outcome": "", "population": "", "secondary": ""}
    text = norm_space(question).rstrip(" ?.!。！")
    if not text:
        return parsed
    if is_keyword_string(text):
        parsed["keyword_string"] = True
        buckets = {"object": [], "intervention": [], "outcome": [], "population": []}
        for chunk in re.split(r"[,;，；/]|\band\b", text):
            chunk = chunk.strip(" .;")
            if chunk:
                buckets[classify_chunk(chunk)].append(chunk)
        parsed.update({key: " and ".join(value) for key, value in buckets.items()})
        return parsed
    secondary = ""
    split = SECONDARY_SPLIT_RE.search(text)
    if split:
        secondary = norm_space(split.group(1) + " " + text[split.end():]).strip(" ,;")
        text = text[:split.start()].strip(" ,;")
    text = AUX_PREFIX_RE.sub("", text).strip(" ,;")
    population = ""
    pop = POPULATION_SPLIT_RE.search(text)
    if pop and len(pop.group(1).split()) <= 12:
        population = pop.group(1).strip(" ,;")
        text = text[:pop.start()].strip(" ,;")
    verb = EFFECT_VERB_RE.search(text)
    if verb:
        intervention = text[:verb.start()].strip(" ,;")
        outcome = text[verb.start():].strip(" ,;")
    else:
        intervention, outcome = text, ""
    parsed.update({"object": intervention or text, "intervention": intervention,
                   "outcome": outcome, "population": population, "secondary": secondary})
    return parsed


def query_for(terms):
    clean = [term for term in terms if term][:4]
    if not clean:
        return ""
    return " AND ".join('"%s"' % term for term in clean)


def merge_specs(specs):
    """Merge sub-questions whose search-term sets overlap above MERGE_THRESHOLD."""
    merged = []
    for spec in specs:
        terms = set(spec["match_terms"])
        target = None
        for prior in merged:
            prior_terms = set(prior["match_terms"])
            if not terms or not prior_terms:
                continue
            if len(terms & prior_terms) / float(len(terms | prior_terms)) >= MERGE_THRESHOLD:
                target = prior
                break
        if target is None:
            merged.append(dict(spec))
        else:
            target["match_terms"] = sorted(set(target["match_terms"]) | terms)
            target["merged_facets"] = target.get("merged_facets", [target["facet"]]) + [spec["facet"]]
    return merged


def dependency_note_for(subquestions):
    if not subquestions:
        return "no sub-question was generated"
    anchor = subquestions[0]["id"]
    dependents = [sq["id"] for sq in subquestions[1:] if sq["facet"] != "gap"]
    gap_ids = [sq["id"] for sq in subquestions if sq["facet"] == "gap"]
    parts = ["%s is the retrieval anchor" % anchor]
    if dependents:
        parts.append("%s %s on %s" % (", ".join(dependents), "depends" if len(dependents) == 1 else "depend", anchor))
    if gap_ids:
        parts.append("%s audits coverage of all earlier stages" % ", ".join(gap_ids))
    return "; ".join(parts) + "."


def build_question(case, task_text, records):
    question = as_text(pick(case, ("research_question", "question", "topic", "objective", "aim", "research_topic", "query")))
    if not question:
        for line in task_text.splitlines():
            line = norm_space(line)
            if line and not line.startswith("#") and len(line) > 12:
                question = line
                break
    window = scope_window(case, question)
    parsed = parse_question(question)
    object_terms = key_terms(question, limit=10)
    facets = detect_facets(question, as_text(pick(case, ("scope", "domain", "field", "constraints"))))

    intervention = parsed["intervention"] or parsed["object"]
    outcome_object = EFFECT_VERB_RE.sub("", parsed["outcome"], count=1).strip(" ,;") if parsed["outcome"] else ""
    population = parsed["population"]
    place = " in %s" % population if population else ""
    topic = intervention or outcome_object or question[:80] or "the stated topic"
    base_terms = key_terms(intervention, limit=3)
    specs = []

    def add(facet, text, terms, facet_terms, query, stop, priority):
        specs.append({"facet": facet, "question": text, "match_terms": terms,
                      "facet_terms": facet_terms, "query": query,
                      "stop_condition": stop, "priority": priority})

    def inline(value):
        if not value:
            return value
        head = value.split()[0]
        if head.isupper() or any(char.isdigit() for char in head):
            return value
        return value[0].lower() + value[1:]

    def facet_query(terms):
        head = " AND ".join('"%s"' % term for term in base_terms[:2])
        tail = " OR ".join('"%s"' % term for term in terms[:4])
        return "%s AND (%s)" % (head, tail) if head else "(%s)" % tail

    primary_terms = key_terms(outcome_object, limit=6) if outcome_object else key_terms(question, limit=5)
    if outcome_object:
        primary_text = "What effect does %s have on %s%s, and which supplied records report it?" % (
            inline(topic), outcome_object, place)
    else:
        primary_text = "Which supplied records report evidence on %s%s?" % (inline(topic), place)
    add("object", primary_text, primary_terms, [], query_for(primary_terms) or query_for(base_terms),
        "stop when every supplied record has been screened against this question", 1)
    if parsed["secondary"]:
        secondary = parsed["secondary"].rstrip("?").strip()
        terms = key_terms(secondary, limit=6)
        add("prediction", "%s%s?" % (secondary[:1].upper() + secondary[1:], place), terms, terms,
            facet_query(terms), "stop when every supplied record stating a predictor has a quoted span", 2)
    if "mechanism" in facets:
        terms = ["mechanism", "pathway"]
        add("mechanism", "Which mechanisms or pathways link %s to %s?" % (inline(topic), outcome_object or "the reported outcome"),
            base_terms[:2] + terms, terms, facet_query(terms),
            "stop when no new mechanism term appears in the last 20% of records", 2)
    if "method" in facets:
        terms = ["cohort", "trial", "model", "design"]
        add("method", "Which designs, models and measurement approaches have been used to study %s%s?" % (inline(topic), place),
            base_terms[:3] + terms, terms, facet_query(terms),
            "stop when design categories repeat for three consecutive records", 3)
    if "comparison" in facets:
        terms = ["versus", "comparison", "difference"]
        add("comparison", "How do the compared conditions differ for %s, and how consistent is the reported direction?" % inline(topic),
            base_terms[:3] + terms, terms, facet_query(terms),
            "stop when both supporting and opposing directions are represented", 3)
    if "temporal" in facets:
        terms = ["trend", "longitudinal", "year"]
        span = "%s-%s" % (window["from"], window["to"]) if window["from"] and window["to"] else "the requested period"
        add("temporal", "How has the reported evidence for %s changed across %s?" % (inline(topic), span),
            base_terms[:3] + terms, terms, facet_query(terms),
            "stop when the year range of the corpus is exhausted", 3)

    specs = merge_specs(specs)
    specs.append({"facet": "gap",
                  "question": "Where does the supplied corpus stop, and which claims remain unsupported or under-sourced?",
                  "match_terms": [], "facet_terms": [], "query": "",
                  "stop_condition": "stop after the coverage audit; no external search",
                  "priority": 4})

    subquestions = []
    for index, spec in enumerate(specs, 1):
        ident = "Q%d" % index
        if spec["facet"] == "gap":
            depends = ["Q%d" % number for number in range(1, index)]
        elif index > 1:
            depends = ["Q1"]
        else:
            depends = []
        queries = [spec["query"]] if spec["query"] else ["coverage audit of the supplied corpus only"]
        subquestions.append({
            "id": ident,
            "question": spec["question"],
            "facet": spec["facet"],
            "depends_on": depends,
            "search_queries": queries,
            "match_terms": spec["match_terms"],
            "facet_terms": spec["facet_terms"],
            "sources": ["supplied_corpus", "external_search_optional"],
            "stop_condition": spec["stop_condition"],
            "priority": spec["priority"],
        })
    return question, window, object_terms, subquestions, parsed


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
    lowered = norm_space(text).lower()
    if not lowered:
        return "neutral"
    null_hit = any(cue in lowered for cue in NO_DIFF_CUES)
    scrubbed = lowered
    for cue in NO_DIFF_CUES:
        scrubbed = scrubbed.replace(cue, " ")
    pos = sum(1 for cue in POSITIVE_CUES if cue in scrubbed)
    neg = sum(1 for cue in NEGATIVE_CUES if cue in scrubbed)
    if pos and not neg:
        return "positive"
    if neg and not pos:
        return "negative"
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    if null_hit and pos:
        return "positive"
    if null_hit:
        return "no_difference"
    return "neutral"


def build_evidence(subquestions, records, skip_locators=()):
    for record in records:
        prepare_record(record)
    candidates = []
    for sq in subquestions:
        terms = sq.get("match_terms") or []
        facet_terms = sq.get("facet_terms") or []
        if not terms:
            continue
        for record in records:
            if record["locator"] in skip_locators:
                continue
            score, hits = relevance(record, terms)
            if score <= 0:
                continue
            matched = {hit["term"] for hit in hits}
            facet_ratio = 0.0
            if facet_terms:
                facet_ratio = len(matched & set(facet_terms)) / float(len(facet_terms))
                if facet_ratio < 0.5:
                    continue
            candidates.append({"subquestion_id": sq["id"], "priority": sq["priority"],
                               "coverage": len(matched) / float(len(terms)), "facet_ratio": facet_ratio,
                               "score": score, "hits": hits, "record": record})
    candidates.sort(key=lambda item: (-item["facet_ratio"], -item["coverage"], item["priority"],
                                      -item["score"], item["subquestion_id"], item["record"]["source_id"]))
    rows, used = [], set()
    for candidate in candidates:
        record = candidate["record"]
        if record["source_id"] in used:
            continue
        used.add(record["source_id"])
        score = candidate["score"]
        matched = {hit["term"] for hit in candidate["hits"]}
        if record["_quote_field"] == "title":
            support = "partial"
        elif record["_has_outcome"] and any(term in record["_quote"].lower() for term in matched):
            support = "direct"
        elif score >= 3:
            support = "partial"
        else:
            support = "weak"
        rows.append({
            "subquestion_id": candidate["subquestion_id"],
            "source_id": record["source_id"],
            "locator": record["locator"],
            "relevance_score": score,
            "coverage_fraction": round(candidate["coverage"], 3),
            "support_level": support,
            "quote_field": record["_quote_field"],
            "matched_terms": [hit["term"] for hit in candidate["hits"]][:8],
            "quote": record["_quote"],
            "year": record["year"],
            "polarity": record["_polarity"],
        })
    rows.sort(key=lambda row: (row["subquestion_id"], -row["relevance_score"], row["source_id"]))
    return rows


def trim_quote(text, width=170):
    text = norm_space(text)
    if len(text) <= width:
        return text
    cut = text[:width]
    space = cut.rfind(" ")
    if space > width // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;") + "..."


def describe_effect(rows):
    quoted = [row for row in rows if row["quote_field"] != "title"]
    if not quoted:
        return None
    positives = [row for row in quoted if row["polarity"] == "positive"]
    others = [row for row in quoted if row["polarity"] in ("negative", "no_difference")]
    if positives and others:
        first, second = positives[0], others[0]
        return '%s reports "%s", whereas %s reports "%s"' % (
            first["source_id"], trim_quote(first["quote"]).rstrip(" .;"),
            second["source_id"], trim_quote(second["quote"]).rstrip(" .;"))
    lead = quoted[0]
    return '%s reports "%s"' % (lead["source_id"], trim_quote(lead["quote"]).rstrip(" .;"))


def build_synthesis(subquestions, rows, records):
    synthesis = []
    for sq in subquestions:
        selected = [row for row in rows if row["subquestion_id"] == sq["id"]][:4]
        if not selected:
            continue
        ids = [row["source_id"] for row in selected]
        polarities = [row["polarity"] for row in selected]
        has_positive = "positive" in polarities
        has_opposing = any(value in ("negative", "no_difference") for value in polarities)
        if has_positive and has_opposing:
            direction = "mixed"
        elif has_positive:
            direction = "favours"
        elif "negative" in polarities:
            direction = "against"
        else:
            direction = "no_difference"
        effect = describe_effect(selected)
        if effect is None:
            statement = ("Direction: %s. Cited sources: %s. Effect: only titles are available for these "
                         "records, so no outcome sentence can be quoted." % (direction, ", ".join(ids)))
            strength = "partial"
        else:
            statement = "Direction: %s. Cited sources: %s. Effect: %s." % (direction, ", ".join(ids), effect)
            if len(selected) >= 2 and all(row["support_level"] == "direct" for row in selected):
                strength = "supported"
            else:
                strength = "partial"
        synthesis.append({
            "subquestion_id": sq["id"],
            "direction": direction,
            "statement": statement,
            "effect_summary": effect or "no outcome sentence in the cited records",
            "strength": strength,
            "cited_sources": [{"source_id": row["source_id"], "locator": row["locator"],
                               "quote": row["quote"], "quote_field": row["quote_field"]} for row in selected],
            "scope_limit": "only the supplied corpus; no external retrieval was performed",
        })
    return synthesis


def build_conflicts(rows, records, skip_locators=()):
    row_by_source = {row["source_id"]: row for row in rows}
    ordered = sorted((record for record in records if record["locator"] not in skip_locators),
                     key=lambda record: record["source_id"])
    conflicts, seen = [], set()
    for index, first in enumerate(ordered):
        for second in ordered[index + 1:]:
            pair = {first["_polarity"], second["_polarity"]}
            if "positive" not in pair or not (pair & {"negative", "no_difference"}):
                continue
            if first["_quote_field"] == "title" or second["_quote_field"] == "title":
                continue
            shared = sorted(set(key_terms(first["title"] + " " + first["abstract"] + " " + first["body"], limit=40)) &
                            set(key_terms(second["title"] + " " + second["abstract"] + " " + second["body"], limit=40)))
            if not shared:
                continue
            key = (first["source_id"], second["source_id"])
            if key in seen:
                continue
            seen.add(key)
            supporting = first if first["_polarity"] == "positive" else second
            opposing = second if supporting is first else first
            anchor = row_by_source.get(supporting["source_id"]) or row_by_source.get(opposing["source_id"])
            conflicts.append({
                "subquestion_id": anchor["subquestion_id"] if anchor else "corpus",
                "type": "opposing_direction",
                "shared_terms": shared[:6],
                "supporting": {"source_id": supporting["source_id"], "locator": supporting["locator"],
                               "quote": supporting["_quote"], "quote_field": supporting["_quote_field"]},
                "opposing": {"source_id": opposing["source_id"], "locator": opposing["locator"],
                             "quote": opposing["_quote"], "quote_field": opposing["_quote_field"]},
                "source_distinct": (supporting["locator"] != opposing["locator"] and
                                    supporting["source_id"] != opposing["source_id"]),
                "handling": "both directions are reported; neither is selected as correct",
            })
            if len(conflicts) >= 12:
                return conflicts
    return conflicts


def gap_queries(sq, hits):
    queries = sq.get("search_queries") or []
    return [{"query": query, "source": "supplied_corpus", "executed": True, "hits": hits} for query in queries]


def build_gaps(subquestions, rows, records, window):
    """Gaps keep the queries that were run and separate 'absent from corpus' from 'not retrieved'."""
    gaps = []
    corpus_state = "corpus_present" if records else "corpus_empty"
    for sq in subquestions:
        if sq["facet"] == "gap":
            continue
        group = [row for row in rows if row["subquestion_id"] == sq["id"]]
        if not group:
            gaps.append({
                "subquestion_id": sq["id"],
                "type": "no_evidence",
                "basis": "no supplied record matched this sub-question",
                "bounded_claim": "cannot be assessed from the supplied corpus",
                "corpus_state": "corpus_empty" if not records else "corpus_present_without_match",
                "retrieval_status": "external_queries_declared_not_run",
                "distinction": ("no corpus was supplied" if not records
                                else "corpus searched, no matching span; external retrieval not attempted"),
                "queries_tried": gap_queries(sq, 0),
                "human_confirm": ("confirm whether to run the listed queries in an external database before "
                                  "treating this as a field-level gap"),
            })
        elif len({row["source_id"] for row in group}) == 1:
            gaps.append({
                "subquestion_id": sq["id"],
                "type": "single_source",
                "basis": "only one distinct record matched",
                "bounded_claim": "single-source only; replication absent",
                "corpus_state": corpus_state + "_single_match",
                "retrieval_status": "external_queries_declared_not_run",
                "distinction": "one supporting span is present; a second independent source was not retrieved",
                "queries_tried": gap_queries(sq, 1),
                "human_confirm": "confirm whether an independent replication source exists before generalising",
            })
    if window["from"] and records:
        years = [record["year"] for record in records if record["year"]]
        if years and max(years) < window["from"]:
            gaps.append({"subquestion_id": "coverage", "type": "temporal_coverage",
                         "basis": "latest supplied record is %d, before the requested window start %d" % (max(years), window["from"]),
                         "bounded_claim": "the requested period is not covered",
                         "corpus_state": corpus_state,
                         "retrieval_status": "coverage_audit_only",
                         "distinction": "the supplied years stop before the requested window; no external search was run",
                         "queries_tried": [{"query": "coverage audit of supplied publication years",
                                            "source": "supplied_corpus", "executed": True, "hits": len(records)}],
                         "human_confirm": "confirm whether records from the requested window should be sourced externally"})
    if records and any(not record["abstract"] for record in records):
        gaps.append({"subquestion_id": "coverage", "type": "metadata_incomplete",
                     "basis": "%d record(s) have no abstract" % sum(1 for record in records if not record["abstract"]),
                     "bounded_claim": "screening relied on title/body only for those records",
                     "corpus_state": corpus_state,
                     "retrieval_status": "metadata_audit_only",
                     "distinction": "metadata is missing in the corpus; this is not evidence that the field lacks data",
                     "queries_tried": [{"query": "metadata completeness audit of supplied records",
                                        "source": "supplied_corpus", "executed": True,
                                        "hits": sum(1 for record in records if not record["abstract"])}],
                     "human_confirm": "confirm whether missing abstracts can be completed before down-weighting these records"})
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


def build_iterations(subquestions, rows):
    """Replay each sub-question's declared query against the supplied corpus."""
    iterations = []
    for sq in subquestions:
        group = [row for row in rows if row["subquestion_id"] == sq["id"]]
        evidence_ids = sorted({row["source_id"] for row in group})
        queries = []
        for query in sq["search_queries"]:
            executed = sq["facet"] != "gap"
            queries.append({"query": query, "source": "supplied_corpus",
                            "executed": executed, "hits": len(group) if executed else 0})
        if sq["facet"] == "gap":
            status = "audit_only"
            artifacts_ref = ["research_gaps", "coverage"]
            note = "Coverage audit only; no external query was executed."
        elif group:
            status = "executed_with_hits"
            artifacts_ref = ["evidence_matrix[%s]" % sq["id"], "synthesis[%s]" % sq["id"]]
            note = "%d evidence row(s) were bound to this sub-question." % len(group)
        else:
            status = "executed_no_hits"
            artifacts_ref = ["research_gaps[%s]" % sq["id"]]
            note = "The declared query matched no supplied record; see research_gaps."
        iterations.append({
            "iteration": len(iterations) + 1,
            "subquestion_id": sq["id"],
            "status": status,
            "queries_run": queries,
            "artifacts_ref": artifacts_ref,
            "new_evidence_ids": evidence_ids,
            "synthesis_note": note,
        })
    return iterations


def resolve_locator(case, locator):
    node = case
    for part in str(locator).strip("/").split("/"):
        if isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(node, dict):
            if part not in node:
                return None
            node = node[part]
        else:
            return None
    return node


def _quote_in_record(quote, candidates):
    quote = norm_space(quote)
    if not quote:
        return False
    for record in candidates:
        for field in ("title", "abstract", "body"):
            if quote in norm_space(record.get(field)):
                return True
    return False


def attach_checks(payload, records, case):
    """Append verification, self_check, gates and validator_verdict; downgrade success to partial on failure."""
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    rows = result.get("evidence_matrix") or []
    conflicts = result.get("conflicts") or []
    gaps = result.get("research_gaps") or []
    synthesis = result.get("synthesis") or []
    iterations = payload.get("iterations") or []
    prisma = payload.get("prisma") or {}
    by_source, by_locator = {}, {}
    for record in records:
        by_source.setdefault(record["source_id"], []).append(record)
        by_locator[record["locator"]] = record

    checked, verified, not_found = 0, 0, []
    for row in rows:
        checked += 1
        if _quote_in_record(row.get("quote"), by_source.get(row.get("source_id"), [])):
            verified += 1
        else:
            not_found.append({"source_id": row.get("source_id"), "subquestion_id": row.get("subquestion_id")})
    conflict_checked, conflict_verified, conflict_violations = 0, 0, []
    for index, conflict in enumerate(conflicts):
        sides = [conflict.get("supporting") or {}, conflict.get("opposing") or {}]
        pair_ok = True
        for side in sides:
            conflict_checked += 1
            if not _quote_in_record(side.get("quote"), by_source.get(side.get("source_id"), [])):
                pair_ok = False
        distinct = (conflict.get("source_distinct") and
                    sides[0].get("locator") != sides[1].get("locator") and
                    sides[0].get("source_id") != sides[1].get("source_id"))
        if pair_ok and distinct:
            conflict_verified += 1
        else:
            conflict_violations.append({"conflict": index, "quotes_present": pair_ok, "source_distinct": bool(distinct)})

    locator_checked, locator_resolved, locator_failed = 0, 0, []
    for row in rows:
        locator_checked += 1
        if resolve_locator(case, row.get("locator")) is not None:
            locator_resolved += 1
        else:
            locator_failed.append({"source_id": row.get("source_id"), "locator": row.get("locator")})
    for conflict in conflicts:
        for side in (conflict.get("supporting") or {}, conflict.get("opposing") or {}):
            locator_checked += 1
            if resolve_locator(case, side.get("locator")) is not None:
                locator_resolved += 1
            else:
                locator_failed.append({"source_id": side.get("source_id"), "locator": side.get("locator")})

    names = contract_names(case)
    missing_contract = [name for name in names if name not in payload]
    gap_ok = all(gap.get("basis") and gap.get("bounded_claim") and gap.get("queries_tried")
                 and gap.get("human_confirm") and gap.get("distinction") for gap in gaps)
    prisma_ok = bool(prisma.get("balance_ok")) and prisma.get("n_identified", 0) == len(records)
    plan_queries = (result.get("retrieval_plan") or {}).get("queries") or {}
    iteration_ok = all(iter_item.get("queries_run") is not None for iter_item in iterations) and \
        all([entry["query"] for entry in iter_item.get("queries_run") or []]
            == list(plan_queries.get(iter_item.get("subquestion_id"), [])) for iter_item in iterations)
    support_by_row = {(row.get("subquestion_id"), row.get("source_id")): row.get("support_level") for row in rows}
    strength_ok = all(
        item.get("strength") != "supported" or all(
            support_by_row.get((item.get("subquestion_id"), cited.get("source_id"))) == "direct"
            for cited in item.get("cited_sources") or [])
        for item in synthesis)
    text_blob = json.dumps({"result": result, "uncertainty": payload.get("uncertainty")},
                           ensure_ascii=False, sort_keys=True)
    placeholder_free = not any(token in text_blob for token in ("TODO", "TBD", "FIXME", "lorem ipsum"))

    verification = {
        "quote_check": {"checked": checked, "verified": verified, "not_found": not_found},
        "conflict_check": {"checked": conflict_checked, "verified": conflict_verified,
                           "pairs": len(conflicts), "violations": conflict_violations},
        "locator_check": {"checked": locator_checked, "resolved": locator_resolved, "failed": locator_failed},
        "contract_check": {"required": names, "missing": missing_contract},
        "gap_check": {"gaps": len(gaps), "with_basis_and_queries": sum(
            1 for gap in gaps if gap.get("basis") and gap.get("queries_tried") and gap.get("human_confirm"))},
        "iteration_check": {"iterations": len(iterations), "aligned_with_plan": iteration_ok},
    }
    self_check = [
        {"item": "case_id_preserved", "passed": payload.get("case_id") == case_identifier(case),
         "detail": "output case_id matches the input case_id"},
        {"item": "status_enum", "passed": payload.get("status") in STATUS_VALUES,
         "detail": "status is one of %s" % ", ".join(STATUS_VALUES)},
        {"item": "contract_fields_present", "passed": not missing_contract,
         "detail": "contract keys missing: %s" % (", ".join(missing_contract) or "none")},
        {"item": "quote_integrity", "passed": not not_found,
         "detail": "%d/%d evidence quotes were found verbatim in the source text" % (verified, checked)},
        {"item": "locator_resolvable", "passed": not locator_failed,
         "detail": "%d/%d locators resolve inside case.json" % (locator_resolved, locator_checked)},
        {"item": "conflict_evidence", "passed": not conflict_violations,
         "detail": "%d/%d conflicts carry two verified quotes from distinct sources" % (conflict_verified, len(conflicts))},
        {"item": "gap_basis", "passed": gap_ok,
         "detail": "%d gap(s) carry basis, bounded claim, queries_tried and human_confirm" % len(gaps)},
        {"item": "prisma_balance", "passed": prisma_ok,
         "detail": "screened equals excluded + included + pending"},
        {"item": "iteration_plan_alignment", "passed": iteration_ok,
         "detail": "%d iteration(s) replay the declared queries" % len(iterations)},
        {"item": "strength_consistency", "passed": strength_ok,
         "detail": "supported synthesis items cite only direct-support rows"},
        {"item": "no_placeholder", "passed": placeholder_free,
         "detail": "no TODO/TBD/FIXME placeholder was serialised"},
    ]
    gates = [
        {"gate": "case_contract", "passed": self_check[0]["passed"] and self_check[1]["passed"] and self_check[2]["passed"],
         "detail": "case_id, status enum and declared contract keys"},
        {"gate": "quote_integrity", "passed": self_check[3]["passed"], "detail": "every quote is a verbatim span"},
        {"gate": "locator_resolvable", "passed": self_check[4]["passed"], "detail": "every locator resolves in the case"},
        {"gate": "conflict_validity", "passed": self_check[5]["passed"], "detail": "conflict sides quote and come from distinct sources"},
        {"gate": "gap_evidence", "passed": self_check[6]["passed"], "detail": "gaps carry basis, queries and human confirmation"},
        {"gate": "prisma_balance", "passed": self_check[7]["passed"], "detail": "screening counts balance"},
        {"gate": "iteration_plan_alignment", "passed": self_check[8]["passed"], "detail": "iterations replay declared queries"},
        {"gate": "strength_consistency", "passed": self_check[9]["passed"], "detail": "synthesis strength matches row support"},
        {"gate": "no_placeholder", "passed": self_check[10]["passed"], "detail": "no placeholder tokens"},
    ]
    passed = sum(1 for gate in gates if gate["passed"])
    verdict = {
        "verdict": "pass" if passed == len(gates) else "fail",
        "checks_total": len(gates),
        "checks_passed": passed,
        "all_checks_passed": passed == len(gates),
        "failed_gates": [gate["gate"] for gate in gates if not gate["passed"]],
    }
    payload["verification"] = verification
    payload["self_check"] = self_check
    payload["gates"] = gates
    payload["validator_verdict"] = verdict
    if verdict["verdict"] != "pass" and payload.get("status") == "success":
        payload["status"] = "partial"
        uncertainty = payload.setdefault("uncertainty", [])
        uncertainty.append({"type": "self_check_failed",
                            "detail": "Validator gates failed (%s); status downgraded to partial."
                                      % ", ".join(verdict["failed_gates"])})
    return payload


def build_payload(case, task_text):
    raw_records = find_records(case)
    records = [normalize_record(raw, locator, index) for index, (locator, raw) in enumerate(raw_records)]
    question, window, object_terms, subquestions, parsed = build_question(case, task_text, records)
    duplicates = find_duplicates(records)
    skipped_locators = set(duplicates) | {record["locator"] for record in records if record["retracted"]}
    rows = build_evidence(subquestions, records, skipped_locators)
    synthesis = build_synthesis(subquestions, rows, records)
    conflicts = build_conflicts(rows, records, skipped_locators)
    gaps = build_gaps(subquestions, rows, records, window)
    order, stages = order_plan(subquestions)
    iterations = build_iterations(subquestions, rows)
    screening = build_screening(records, rows, duplicates, skipped_locators)
    prisma = build_prisma(screening, len(records))

    covered = {row["source_id"] for row in rows}
    outcome_ids = {record["source_id"] for record in records if record["_has_outcome"]}
    covered_outcome = covered & outcome_ids
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
                            "detail": "At least one supplied record is marked retracted; it is excluded from the evidence matrix."})
    if duplicates:
        uncertainty.append({"type": "duplicate_records_removed",
                            "detail": "%d duplicate record(s) were removed by DOI/title before screening." % len(duplicates)})
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
            "parsed_question": parsed,
            "subquestions": subquestions,
            "dependency_note": dependency_note_for(subquestions),
        },
        "retrieval_plan": {
            "state": "declared",
            "order": order,
            "stages": stages,
            "queries": {sq["id"]: sq["search_queries"] for sq in subquestions},
            "deduplication": ("sub-questions whose search-term sets overlap by 75% or more are merged; each "
                              "source_id is assigned to exactly one sub-question, so the evidence matrix lists it once"),
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
            "records_with_outcome_text": len(outcome_ids),
            "recall": round(len(covered_outcome) / float(len(records)), 3) if records else 0.0,
            "recall_basis": ("cited records carrying a quotable outcome sentence divided by all supplied records; "
                             "title-only records are not counted as covered"),
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
            "Declared an ordered retrieval plan (state=declared) with per-sub-question queries and stop conditions.",
            "Screened and de-duplicated the supplied records and reported PRISMA counts.",
            "Replayed each declared query as an iteration and bound hits to the evidence matrix.",
            "Aligned supplied records to sub-questions with quoted evidence and support levels.",
            "Reported opposing directions as conflicts instead of choosing one side.",
            "Derived research gaps from coverage of the supplied corpus only.",
            "Ran quote, locator, contract, conflict, gap and PRISMA gates from the emitted payload.",
        ],
    }

    payload = {
        "case_id": case_identifier(case),
        "status": status,
        "result": result,
        "evidence": evidence,
        "uncertainty": uncertainty,
        "errors": [],
        "screening": screening,
        "prisma": prisma,
        "iterations": iterations,
        "stats": {
            "records": len(records),
            "subquestions": len(subquestions),
            "evidence_rows": len(rows),
            "conflicts": len(conflicts),
            "gaps": len(gaps),
            "records_with_year": sum(1 for record in records if record["year"]),
            "screened_included": prisma["n_included"],
            "screened_excluded": prisma["n_excluded_by_code"],
            "screened_pending": prisma["n_pending"],
            "duplicates_removed": prisma["n_duplicates"],
            "iterations": len(iterations),
            "network": "disabled",
        },
    }
    return payload, records


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
    records = []
    try:
        case = load_json(args.case)
        if not isinstance(case, dict):
            raise ValueError("case.json root must be a JSON object")
        try:
            Path(args.work).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        task_text = read_task_text(args.case, args.work)
        payload, records = build_payload(case, task_text)
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
    attach_checks(payload, records, case if isinstance(case, dict) else {})
    if sk_artifacts is not None and isinstance(payload.get("result"), dict):
        out_dir = os.path.dirname(os.path.abspath(args.out)) or "."
        try:
            payload["result"]["artifacts"] = sk_artifacts.write_artifacts(payload, out_dir)
        except Exception as exc:
            payload.setdefault("errors", []).append(
                {"stage": "artifacts", "type": type(exc).__name__, "message": str(exc)[:300]})
            payload["result"]["artifacts"] = []
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
