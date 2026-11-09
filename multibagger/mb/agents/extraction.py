"""Extraction agent: turns watchlist filings and company documents into structured facts with citations
(`facts/`).

Filings: the announcement text, plus the attached PDF when it can be fetched (exchange archives may refuse
cloud servers; then the text alone is used and the fact records say so).

Documents: annual reports, investor presentations and concall transcripts that the desktop task drops in
the bucket at inbox/docs/<SYMBOL>/<type>-<period>.pdf for the stocks on docs/wanted.json. Each is read
once (one model call) and moved to docs/<SYMBOL>/. Annual reports and results are P1 sources; concall
transcripts and presentations are P2 (management's own words).
"""
from __future__ import annotations

import io
import os

from .. import inputs, llm, requests, sources
from ..store import now_iso
from ._context import GROUND_RULES, compact, eligible, per_run

MAX_FILINGS = int(os.getenv("MB_EXTRACT_MAX_FILINGS", "25"))
MAX_PDF_PAGES = int(os.getenv("MB_EXTRACT_MAX_PDF_PAGES", "40"))
FACT_TYPES = ["guidance", "capex", "order_win", "order_book", "capacity", "utilisation", "customer", "product",
              "related_party", "contingent_liability", "auditor_remark", "management_change", "fund_raise",
              "credit_rating", "insider_trade", "volume_growth", "market_share", "distribution", "realisation",
              "same_store_growth", "asset_quality", "capital_adequacy", "loan_growth", "return_on_assets",
              "regulatory", "recurring_revenue", "retention", "presales", "collections", "cost_position",
              "margin", "other"]

PROMPT = """Extract structured facts from these exchange filings of Indian listed companies. Fact types: {types}.
For guidance, capture the metric, the number or range, the period it refers to, and whether it updates an
earlier guidance. Ignore routine items (trading window closures, newspaper ad copies) unless they say
something material. Each fact cites its filing id and, when from the PDF, the page.

Filings:
{filings}

Return {{"facts": [{{"filing_id": "...", "symbol": "...", "type": "...", "statement": "one sentence",
"metric": "...", "value": "...", "unit": "...", "period": "...", "period_start": "YYYY-MM-DD or null",
"period_end": "YYYY-MM-DD or null", "page": n or null, "material": true|false}}]}}"""


def _pdf_text(url: str) -> tuple[str | None, str]:
    try:
        from pypdf import PdfReader

        data = sources.PoliteSession().get(url).content
        reader = PdfReader(io.BytesIO(data))
        pages = reader.pages[:MAX_PDF_PAGES]
        note = f"pages 1-{len(pages)} of {len(reader.pages)}"
        return "\n".join(f"[page {i + 1}] {p.extract_text() or ''}" for i, p in enumerate(pages)), note
    except Exception as exc:
        return None, f"attachment not readable: {type(exc).__name__}"


DOC_INBOX = "inbox/docs/"
MAX_DOC_PAGES = int(os.getenv("MB_EXTRACT_MAX_DOC_PAGES", "150"))
MAX_DOC_CHARS = int(os.getenv("MB_EXTRACT_MAX_DOC_CHARS", "180000"))
DOC_KINDS = [("annual", "annual report", "P1"), ("result", "results filing", "P1"),
             ("concall", "concall transcript", "P2"), ("transcript", "concall transcript", "P2"),
             ("presentation", "investor presentation", "P2"), ("investor", "investor presentation", "P2"),
             ("ppt", "investor presentation", "P2"), ("rating", "rating rationale", "P3")]

DOC_PROMPT = """Extract the facts that matter for a multibagger thesis from this {kind} of {symbol} ({name}).
Sector playbook: {playbook}. Prioritise the playbook's critical signals and sector-critical data
({critical}), then guidance (metric, number, period, whether it revises earlier guidance), capacity,
utilisation, capex and its funding, order book and inflow, customers and concentration, market share,
volume vs price growth, margins, working capital, related-party dealings, contingent liabilities and
auditor remarks. Fact types: {types}. Quote numbers exactly; cite the page. Skip boilerplate.

Document ({note}):
{text}

Return {{"facts": [{{"type": "...", "statement": "one sentence", "metric": "...", "value": "...", "unit": "...",
"period": "...", "period_start": "YYYY-MM-DD or null", "period_end": "YYYY-MM-DD or null", "page": n or null,
"material": true|false}}]}} (at most 40 facts, most material first)"""


def doc_kind(name: str) -> tuple[str, str]:
    low = name.lower()
    return next(((label, grade) for key, label, grade in DOC_KINDS if key in low), ("company document", "P2"))


def _pdf_bytes_text(data: bytes, max_pages: int, max_chars: int) -> tuple[str, str]:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    parts, n = [], 0
    for i, p in enumerate(reader.pages[:max_pages]):
        t = f"[page {i + 1}] {p.extract_text() or ''}"
        if n + len(t) > max_chars:
            break
        parts.append(t)
        n += len(t)
    return "\n".join(parts), f"pages 1-{len(parts)} of {len(reader.pages)}"


def _extract_doc(store, path: str) -> int:
    from ..blob import get_blob
    from ..playbooks import CRITICAL_DATA, brief
    blob = get_blob()
    sym, name = path.split("/")[-2].upper(), path.rsplit("/", 1)[-1]
    kind, grade = doc_kind(name)
    data = blob.get(path) or b""
    if name.lower().endswith(".pdf"):
        text, note = _pdf_bytes_text(data, MAX_DOC_PAGES, MAX_DOC_CHARS)
    else:
        text, note = data.decode("utf-8", "replace")[:MAX_DOC_CHARS], "text file"
    cand = store.get("candidates", sym) or {}
    pb = cand.get("playbook") or "general"
    out = llm.complete_json(GROUND_RULES, DOC_PROMPT.format(
        kind=kind, symbol=sym, name=cand.get("name") or sym, playbook=compact(brief(pb), 2000),
        critical=", ".join(c[0] for c in CRITICAL_DATA.get(pb, [])) or "none listed",
        types=", ".join(FACT_TYPES), note=note, text=text or "(no text could be read)"), agent="extraction", store=store)
    stamp, facts = now_iso(), {}
    dest = f"docs/{sym}/{name}"
    doc_id = f"doc-{sym}-{name.rsplit('.', 1)[0]}"[:120]
    for i, x in enumerate((out.get("facts") or [])[:40]):
        facts[f"{doc_id}-{i}"] = {**x, "symbol": sym, "filing_id": doc_id, "extracted": stamp, "source_type": kind,
                                  "source_grade": grade, "source_doc": dest, "verification_status": "extracted",
                                  "human_verified": False, "point_in_time_valid": True,
                                  "extraction_model_version": llm.get_settings(store).get("model")}
    store.set_many("facts", facts)
    blob.move(path, dest)
    store.set("documents", doc_id, {"symbol": sym, "file": dest, "kind": kind, "source_grade": grade,
                                    "facts": len(facts), "note": note, "extracted": stamp})
    return len(facts)


def _extract(store, pending: list[dict]) -> int:
    """One model call over a batch of filings; marks them extracted. Returns the facts saved."""
    batch = []
    for f in pending:
        text, note = (None, "no attachment")
        if f.get("attachment"):
            text, note = _pdf_text(f["attachment"])
        batch.append({"filing_id": f["id"], "symbol": f.get("symbol"), "date": f.get("date"),
                      "category": f.get("category"), "subject": f.get("subject"), "attachment_note": note,
                      "attachment_text": text})
    out = llm.complete_json(GROUND_RULES, PROMPT.format(types=", ".join(FACT_TYPES),
                                                       filings=compact(batch, 150000)), agent="extraction",
                            store=store)
    stamp, facts = now_iso(), {}
    ids = {b["filing_id"] for b in batch}
    by_id = {f["id"]: f for f in pending}
    for i, x in enumerate(out.get("facts", [])):
        if x.get("filing_id") in ids:
            src = by_id[x["filing_id"]]
            facts[f"{x['filing_id']}-{i}"] = {
                **x, "extracted": stamp, "source_type": "exchange filing", "source_grade": "P1",
                "published": src.get("date"), "source_url": src.get("attachment"),
                "verification_status": "extracted", "human_verified": False,
                "point_in_time_valid": True, "extraction_model_version": llm.get_settings(store).get("model")}
    store.set_many("facts", facts)
    store.set_many("filings", {f["id"]: {"extracted": stamp} for f in pending}, merge=True)
    return len(facts)


def run(store, log) -> dict:
    """Company documents first (they are the richest evidence), then the unprocessed filings of the
    strongest eligible watchlist stocks: up to the Settings "stocks per run" count, in calls of at most
    MB_EXTRACT_MAX_FILINGS filings each. Stocks requested on the stock page go first; a per-stock run
    (MB_SYMBOLS) handles just those stocks, eligible or not."""
    from ..blob import get_blob
    only = inputs.only_symbols()
    asked = only or set(requests.queued(store, "extraction"))
    docs = [p for p in get_blob().list(DOC_INBOX) if p.count("/") >= 3 and (not only or p.split("/")[-2].upper() in only)]
    ranked = [c["id"] for c in eligible(store)]
    ranked = sorted(asked) + [s for s in ranked if s not in asked and not only]
    by_sym: dict[str, list] = {}
    for f in store.list("filings", order_by="ingested", limit=5000):
        if not f.get("extracted"):
            by_sym.setdefault(f.get("symbol"), []).append(f)
    with_work = [sym for sym in ranked if sym in by_sym]
    picked = with_work[:max(per_run(store, "extraction"), len(asked))]
    queue = [f for sym in picked for f in by_sym[sym]]
    done, facts, stopped, doc_done = 0, 0, None, 0
    for path in docs:
        if inputs.out_of_time():
            stopped = "time"
            break
        try:
            facts += _extract_doc(store, path)
            doc_done += 1
            log.info("read %s", path)
        except llm.BudgetExceeded as exc:
            stopped = "daily cap"
            log.warning("%s", exc)
            break
        except Exception as exc:
            log.error("%s: %s", path, exc)
    while done < len(queue) and not stopped:
        if inputs.out_of_time():
            stopped = "time"
            break
        chunk = queue[done:done + MAX_FILINGS]
        try:
            facts += _extract(store, chunk)
        except llm.BudgetExceeded as exc:
            stopped = "daily cap"
            log.warning("%s", exc)
            break
        done += len(chunk)
        log.info("extracted %d filings so far (%d facts)", done, facts)
    if not stopped:
        requests.finish(store, "extraction", asked, f"{facts} facts")
    left_stocks = len(with_work) - len(picked) + len({f.get("symbol") for f in queue[done:]})
    if not queue and not docs:
        log.info("nothing to extract: no unprocessed filings or documents for eligible watchlist names")
    log.info("extracted %d facts from %d documents and %d filings of %d stocks", facts, doc_done, done, len(picked))
    return {"documents": doc_done, "filings": done, "facts": facts, "stocks": len(picked),
            "stocks_still_to_do": left_stocks, "stopped_by": stopped,
            "more_to_do": bool((done or doc_done) and left_stocks and stopped != "daily cap" and not only)}
