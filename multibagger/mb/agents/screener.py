"""Screener agent: Pass 1 quantitative screen over `fundamentals/`, results in `candidates/`."""
from __future__ import annotations

from ..rules import pass1
from ..store import now_iso


def run(store, log) -> dict:
    funds = store.list("fundamentals")
    if not funds:
        raise RuntimeError("no fundamentals loaded yet: upload a Screener export on the portal's Data page")
    hard = {a["symbol"] for a in store.list("alerts", where=[("severity", "==", "hard"), ("status", "==", "open")])}
    stamp, docs, passed = now_iso(), {}, 0
    previous = {c["id"] for c in store.list("candidates", where=[("passed", "==", True)])}
    for f in funds:
        sym = f["id"]
        res = pass1(f)
        if sym in hard:
            res["passed"] = False
            res["blocked_by"] = "open hard red flag"
        passed += res["passed"]
        docs[sym] = {"symbol": sym, "name": f.get("name"), "mcap": f.get("mcap"), "pe": f.get("pe"),
                     **res, "screened": stamp}
    store.set_many("candidates", docs)
    now_passed = {k for k, v in docs.items() if v["passed"]}
    added, dropped = sorted(now_passed - previous), sorted(previous - now_passed)
    store.set("screens", stamp[:10], {"date": stamp, "screened": len(docs), "passed": passed,
                                      "added": added, "dropped": dropped})
    log.info("screened %d, %d passed (+%d / -%d)", len(docs), passed, len(added), len(dropped))
    return {"screened": len(docs), "passed": passed, "added": added[:50], "dropped": dropped[:50]}
