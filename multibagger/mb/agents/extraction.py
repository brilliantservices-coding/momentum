"""Extraction agent: turns watchlist filings into structured facts with citations (`facts/`).

Works from the announcement text, plus the attached PDF when it can be fetched (exchange archives
may refuse cloud servers; then the text alone is used and the fact records say so).
"""
from __future__ import annotations

import io
import os

from .. import llm, sources
from ..store import now_iso
from ._context import GROUND_RULES, compact

MAX_FILINGS = int(os.getenv("MB_EXTRACT_MAX_FILINGS", "25"))
MAX_PDF_PAGES = int(os.getenv("MB_EXTRACT_MAX_PDF_PAGES", "40"))
FACT_TYPES = ["guidance", "capex", "order_win", "order_book", "capacity", "utilisation", "customer", "product",
              "related_party", "contingent_liability", "auditor_remark", "management_change", "fund_raise",
              "credit_rating", "insider_trade", "other"]

PROMPT = """Extract structured facts from these exchange filings of Indian listed companies. Fact types: {types}.
For guidance, capture the metric, the number or range, the period it refers to, and whether it updates an
earlier guidance. Ignore routine items (trading window closures, newspaper ad copies) unless they say
something material. Each fact cites its filing id and, when from the PDF, the page.

Filings:
{filings}

Return {{"facts": [{{"filing_id": "...", "symbol": "...", "type": "...", "statement": "one sentence",
"metric": "...", "value": "...", "period": "...", "page": n or null, "material": true|false}}]}}"""


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


def run(store, log) -> dict:
    watch = {c["id"] for c in store.list("candidates", where=[("passed", "==", True)])}
    pending = [f for f in store.list("filings", order_by="ingested", limit=2000)
               if f.get("symbol") in watch and not f.get("extracted")][:MAX_FILINGS]
    if not pending:
        log.info("nothing to extract: no unprocessed filings for watchlist names")
        return {"filings": 0, "facts": 0}
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
    for i, x in enumerate(out.get("facts", [])):
        if x.get("filing_id") in {b["filing_id"] for b in batch}:
            facts[f"{x['filing_id']}-{i}"] = {**x, "extracted": stamp}
    store.set_many("facts", facts)
    store.set_many("filings", {f["id"]: {"extracted": stamp} for f in pending}, merge=True)
    log.info("extracted %d facts from %d filings", len(facts), len(pending))
    return {"filings": len(pending), "facts": len(facts)}
