"""Screener agent: Pass 1 quantitative screen over `fundamentals/`, results in `candidates/`.

Strategy v2: every company is also ranked against its own industry (percentiles), recent positive filing
triggers count as inflection evidence, and a company qualifies through either the Compounder or the
Inflection lane. It also publishes the list of stocks whose annual reports, presentations and concall
transcripts the desktop task should fetch (docs/wanted.json in the bucket).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .. import inputs
from ..peers import percentiles
from ..rules import pass1
from ..store import now_iso

TRIGGER_DAYS = 180
DOCS_WANTED = 50


def recent_triggers(store) -> dict[str, list]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=TRIGGER_DAYS)).isoformat(timespec="seconds")
    out: dict[str, list] = {}
    for t in store.list("triggers"):
        if (t.get("created") or "") >= cutoff and t.get("symbol"):
            out.setdefault(t["symbol"], []).append(t)
    for v in out.values():
        v.sort(key=lambda t: t.get("created") or "", reverse=True)
    return out


def run(store, log) -> dict:
    from ..blob import get_blob
    from ..fundamentals import import_inbox
    only = inputs.only_symbols()
    if not only and import_inbox(store, get_blob(), log):  # Trendlyne export from the Friday desktop task
        from . import forensic
        forensic.run(store=store, log=log)
    funds = store.list("fundamentals")
    if not funds:
        raise RuntimeError("no fundamentals loaded yet: upload a Screener export on the portal's Data page")
    hard = {a["symbol"] for a in store.list("alerts", where=[("severity", "==", "hard"), ("status", "==", "open")])}
    pct = percentiles(funds)  # always over everyone, so a one-stock re-screen keeps its peer ranking
    trig = recent_triggers(store)
    analysed_bis = {x["id"]: x.get("bis") for x in store.list("scores") if x.get("bis")}
    stamp, docs, passed = now_iso(), {}, 0
    previous = {c["id"] for c in store.list("candidates", where=[("passed", "==", True)])}
    for f in funds:
        sym = f["id"]
        if only and sym not in only:
            continue
        res = pass1(f, pct.get(sym), trig.get(sym))
        res["pass1_passed"] = res["passed"]
        if analysed_bis.get(sym):  # the analyst's Business Inflection Score, once it exists, also ranks the queue
            res["research_priority"] = round(res["research_priority"] + 0.3 * float(analysed_bis[sym]), 1)
        if sym in hard:
            res["passed"] = False
            res["blocked_by"] = "open hard red flag"
        passed += res["passed"]
        docs[sym] = {"symbol": sym, "name": f.get("name"), "mcap": f.get("mcap"), "pe": f.get("pe"),
                     "industry": f.get("industry"), "sector": f.get("sector"), "peer_pct": pct.get(sym) or {},
                     **res, "screened": stamp}
    store.set_many("candidates", docs)
    if only:
        from .. import requests as req
        req.finish(store, "screener", only, "re-screened")
        log.info("re-screened %s", ", ".join(sorted(docs)))
        return {"screened": len(docs), "passed": passed, "only": sorted(only)}
    now_passed = {k for k, v in docs.items() if v["passed"]}
    added, dropped = sorted(now_passed - previous), sorted(previous - now_passed)
    lanes = {lane: sum(1 for v in docs.values() if v["passed"] and (v.get("lanes") or {}).get(lane))
             for lane in ("compounder", "inflection")}
    store.set("screens", stamp[:10], {"date": stamp, "screened": len(docs), "passed": passed, "lanes": lanes,
                                      "added": added, "dropped": dropped})
    from .. import requests as req
    req.finish(store, "screener", req.queued(store, "screener"), "re-screened (weekly screen)")
    wanted = publish_docs_wanted(store)
    log.info("screened %d, %d passed (compounder %d, inflection %d; +%d / -%d); %d stocks on the documents list",
             len(docs), passed, lanes["compounder"], lanes["inflection"], len(added), len(dropped), len(wanted))
    return {"screened": len(docs), "passed": passed, "lanes": lanes, "added": added[:50], "dropped": dropped[:50],
            "docs_wanted": len(wanted)}


def publish_docs_wanted(store, n: int = DOCS_WANTED) -> list[dict]:
    """The top-n eligible stocks, for the desktop task that downloads their annual report, latest investor
    presentation and concall transcript into inbox/docs/<SYMBOL>/ (see OPERATIONS.md)."""
    from ..blob import get_blob
    from ._context import eligible
    have = {}
    for path in get_blob().list("docs/"):
        parts = path.split("/")
        if len(parts) >= 3:
            have.setdefault(parts[-2], []).append(parts[-1])
    rows = [{"symbol": c["id"], "name": c.get("name"), "lane": c.get("lane"), "playbook": c.get("playbook"),
             "have": sorted(have.get(c["id"], []))} for c in eligible(store)[:n]]
    get_blob().put("docs/wanted.json", json.dumps({"updated": now_iso(), "stocks": rows}, indent=1).encode(),
                   "application/json")
    store.set("control", "docs_wanted", {"updated": now_iso(), "symbols": [r["symbol"] for r in rows]})
    return rows
