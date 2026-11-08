"""Extraction agent: turns watchlist filings into structured facts with citations (`facts/`).

Works from the announcement text, plus the attached PDF when it can be fetched (exchange archives
may refuse cloud servers; then the text alone is used and the fact records say so).
"""
from __future__ import annotations

import io
import os

from .. import inputs, llm, sources
from ..store import now_iso
from ._context import GROUND_RULES, compact, eligible, per_run

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
    """Extracts the unprocessed filings of the strongest eligible watchlist stocks first: up to the
    Settings "stocks per run" count, in calls of at most MB_EXTRACT_MAX_FILINGS filings each."""
    ranked = [c["id"] for c in eligible(store)]
    by_sym: dict[str, list] = {}
    for f in store.list("filings", order_by="ingested", limit=5000):
        if not f.get("extracted"):
            by_sym.setdefault(f.get("symbol"), []).append(f)
    with_work = [sym for sym in ranked if sym in by_sym]
    picked = with_work[:per_run(store, "extraction")]
    queue = [f for sym in picked for f in by_sym[sym]]
    if not queue:
        log.info("nothing to extract: no unprocessed filings for eligible watchlist names")
        return {"filings": 0, "facts": 0, "stocks": 0, "more_to_do": False}
    done, facts, stopped = 0, 0, None
    while done < len(queue):
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
    left_stocks = len(with_work) - len(picked) + len({f.get("symbol") for f in queue[done:]})
    log.info("extracted %d facts from %d filings of %d stocks", facts, done, len(picked))
    return {"filings": done, "facts": facts, "stocks": len(picked), "stocks_still_to_do": left_stocks,
            "stopped_by": stopped, "more_to_do": bool(done and left_stocks and stopped != "daily cap")}
