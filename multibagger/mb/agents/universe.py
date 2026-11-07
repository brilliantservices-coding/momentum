"""Universe builder: the NSE equity list (EQ/BE/SM series) as `universe/{symbol}`."""
from __future__ import annotations

from .. import sources
from ..store import now_iso


def run(store, log) -> dict:
    df = sources.nse_equity_list()
    df = df[df["SERIES"].astype(str).str.strip().isin(["EQ", "BE", "BZ", "SM", "ST"])]
    docs = {}
    for row in df.to_dict("records"):
        sym = str(row["SYMBOL"]).strip().upper()
        docs[sym] = {
            "symbol": sym,
            "name": str(row.get("NAME OF COMPANY", "")).strip(),
            "series": str(row.get("SERIES", "")).strip(),
            "isin": str(row.get("ISIN NUMBER", "")).strip(),
            "listed": str(row.get("DATE OF LISTING", "")).strip(),
            "updated": now_iso(),
        }
    store.set_many("universe", docs, merge=True)
    log.info("universe: %d symbols", len(docs))
    return {"symbols": len(docs)}
