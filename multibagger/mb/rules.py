"""Pass 1 screen and structured red-flag rules from the strategy doc. Pure functions, unit tested."""
from __future__ import annotations

import math


def _get(m, k):
    return m.get(k)


def pass1(m: dict) -> dict:
    """Returns {rule: True|False|None}, gates and an overall verdict for one company."""
    g = lambda k: _get(m, k)  # noqa: E731
    rules: dict[str, bool | None] = {}

    s3, q = g("sales_cagr_3y"), g("qtr_sales_yoy")
    rules["growth"] = None if s3 is None and q is None else bool((s3 or -1e9) >= 15 or (q or -1e9) >= 25)

    roce, roce_avg = g("roce"), g("roce_3y_avg")
    rules["roce"] = None if roce is None else bool(roce >= 15 or (roce_avg is not None and roce >= 10 and roce - roce_avg >= 5))

    cfo, ebitda3 = g("cfo_3y"), g("ebitda_3y")
    rules["cash_conversion"] = None if cfo is None or not ebitda3 or is_financial(m) else bool(ebitda3 > 0 and cfo / ebitda3 >= 0.7)

    debt, cash, ebitda, de = g("debt"), g("cash"), g("ebitda"), g("debt_to_equity")
    lev = None
    if debt is not None and ebitda:
        lev = ebitda > 0 and (debt - (cash or 0)) / ebitda < 2
    elif de is not None:
        lev = de < 1
    sn, s3b = g("shares_now"), g("shares_3y_back")
    if lev is not None and sn and s3b:
        lev = lev and (sn / s3b - 1) < 0.10
    rules["balance_sheet"] = None if lev is None else bool(lev)

    prom, pledge, pch = g("promoter"), g("pledge"), g("promoter_change")
    rules["promoter"] = None if prom is None else bool(prom > 40 and (pledge or 0) < 5 and (pch or 0) >= 0)

    pe = g("pe")
    growth = g("profit_cagr_3y") if g("profit_cagr_3y") is not None else s3
    rules["valuation"] = None if pe is None or growth is None else bool(0 < pe <= max(growth, 0) * 1.5)

    dd, dd3 = g("debtor_days"), g("debtor_days_3y_back")
    rules["working_capital"] = None if dd is None or dd3 is None else bool(dd <= dd3 * 1.1 + 5)

    mcap, liq = g("mcap"), g("avg_traded_value_cr")
    gates = {
        "mcap_range": None if mcap is None else bool(500 <= mcap <= 20000),
        "liquidity": None if liq is None else bool(liq >= 1),
    }
    known = [v for v in rules.values() if v is not None]
    met = sum(known)
    need = max(4, math.ceil(0.7 * len(known)))  # "most of these, not necessarily all"
    passed = len(known) >= 4 and met >= need and all(v is not False for v in gates.values())
    return {"rules": rules, "gates": gates, "met": met, "known": len(known), "passed": passed}


FINANCIAL_WORDS = ("bank", "financ", "nbfc", "insurance", "broker", "capital market", "asset management",
                   "lending", "credit")


def is_financial(m: dict) -> bool:
    """Lenders and brokers: loan growth is an operating cash outflow, so the cash-conversion test doesn't apply."""
    text = f"{m.get('sector') or ''} {m.get('industry') or ''}".lower()
    return any(w in text for w in FINANCIAL_WORDS)


def structured_flags(m: dict) -> list[dict]:
    """Red flags detectable from the fundamentals export. Each: {rule, severity, detail}."""
    flags = []
    g = m.get
    if g("pledge") is not None and g("pledge") > 20:
        flags.append({"rule": "pledge_above_20", "severity": "hard", "detail": f"Promoter pledge {g('pledge'):.1f}%"})
    elif g("pledge") is not None and g("pledge_prev") is not None and g("pledge") > g("pledge_prev") + 1:
        big = g("pledge") - g("pledge_prev") >= 5 or g("pledge") > 10  # small rises from a low base are soft
        flags.append({"rule": "pledge_rising", "severity": "hard" if big else "soft",
                      "detail": f"Pledge up from {g('pledge_prev'):.1f}% to {g('pledge'):.1f}%"})
    if g("cfo_3y") is not None and (g("profit_3y") or 0) > 0 and g("cfo_3y") <= 0.1 * g("profit_3y") \
            and not is_financial(m):
        flags.append({"rule": "profit_without_cash", "severity": "hard",
                      "detail": f"3y operating cash flow {g('cfo_3y'):.0f} vs 3y profit {g('profit_3y'):.0f}"})
    if g("contingent_liabilities") is not None and g("net_worth") and g("contingent_liabilities") > 0.25 * g("net_worth"):
        flags.append({"rule": "contingent_liabilities", "severity": "soft",
                      "detail": f"Contingent liabilities {g('contingent_liabilities'):.0f} > 25% of net worth {g('net_worth'):.0f}"})
    if g("debtor_days") is not None and g("debtor_days_3y_back") and g("debtor_days") > 1.25 * g("debtor_days_3y_back") + 5:
        flags.append({"rule": "receivables_rising", "severity": "soft",
                      "detail": f"Debtor days {g('debtor_days_3y_back'):.0f} → {g('debtor_days'):.0f} over 3 years"})
    if g("cwip") is not None and g("cwip_prev") and g("cwip") > 2 * g("cwip_prev") and g("cwip") > 10:
        flags.append({"rule": "cwip_jump", "severity": "soft",
                      "detail": f"CWIP {g('cwip_prev'):.0f} → {g('cwip'):.0f}"})
    if g("shares_now") and g("shares_3y_back") and g("shares_now") / g("shares_3y_back") - 1 > 0.10:
        flags.append({"rule": "equity_dilution", "severity": "soft",
                      "detail": f"Share count up {100 * (g('shares_now') / g('shares_3y_back') - 1):.0f}% in 3 years"})
    if g("promoter_change") is not None and g("promoter_change") <= -2:
        flags.append({"rule": "promoter_selling", "severity": "soft",
                      "detail": f"Promoter holding change {g('promoter_change'):.1f} pp"})
    return flags


# Keyword rules for exchange announcements: (rule, severity, any-of phrases), matched lower-case.
FILING_RULES = [
    ("auditor_resignation", "hard", ["resignation of statutory auditor", "resignation of auditor", "auditor has resigned",
                                     "statutory auditors resign"]),
    ("qualified_opinion", "hard", ["qualified opinion", "adverse opinion", "disclaimer of opinion"]),
    ("regulatory_action", "hard", ["sebi order", "show cause notice", "enforcement directorate", "sfio",
                                   "search and seizure", "income tax search", "forensic audit"]),
    ("default", "hard", ["default in payment", "delay in payment of interest", "insolvency", "nclt admit"]),
    ("rating_downgrade", "soft", ["downgrade", "rating watch with negative"]),
    ("preferential_issue", "soft", ["preferential allotment", "preferential issue", "convertible warrants"]),
    ("cfo_cs_change", "soft", ["resignation of chief financial officer", "resignation of cfo",
                               "resignation of company secretary"]),
    ("name_change", "soft", ["change of name", "change in name of the company"]),
]


# Context that makes a hard filing match less serious (soft) or a non-event (skipped).
SOFTEN = {"default": ["subsidiary"], "auditor_resignation": ["subsidiary"],
          "regulatory_action": ["commercial taxes", "gst", "goods and services tax", "income tax department"]}
SKIP = {"regulatory_action": ["granting exemption", "exemption order", "order granting"]}


def filing_flags(text: str) -> list[dict]:
    t = (text or "").lower()
    out = []
    for r, sev, phrases in FILING_RULES:
        hit = next((p for p in phrases if p in t), None)
        if hit is None or any(x in t for x in SKIP.get(r, [])):
            continue
        if sev == "hard" and any(x in t for x in SOFTEN.get(r, [])):
            sev = "soft"
        out.append({"rule": r, "severity": sev, "detail": hit})
    return out


# ---- Conviction score (strategy doc: "Combining signals into a confidence score") ----
WEIGHTS = {"theme": 15, "financial": 20, "management": 20, "visibility": 15, "ecosystem": 10,
           "valuation": 10, "governance": 10}
TIERS = [(75, "A"), (62, "B"), (52, "C")]
SIZING = {"A": (4.0, 8.0), "B": (2.5, 5.0), "C": (1.0, 2.0)}  # starter %, max % of portfolio (cost)
STRONG_SHARE = 0.6      # a family is strong at 60%+ of its weight (and verified)
UNVERIFIED_SHARE = 0.5  # a family with no usable evidence gets half its weight
SOFT_FLAG_COST = 3


def tier_for(score: float, unverified: int = 0) -> str:
    """Tier from the score; two or more unverified families cap it at C."""
    tier = next((t for cut, t in TIERS if score >= cut), "Reject")
    return "C" if unverified >= 2 and tier in ("A", "B") else tier


def conviction(families: dict, hard_flags: int = 0, soft_flags: int = 0, unverified=()) -> dict:
    """families: {name: points awarded (0..weight)}; unverified: families with no usable evidence.

    Revised 8 Nov 2026 (strategy doc, "Combining signals into a confidence score"): unverified families
    get 50% of their weight and never count as strong; strong = 60%+ of weight; +5 for 5+ strong
    families, capped at 60 with fewer than 3; each soft flag costs 3 points (3+ reject); a hard flag zeroes
    the score; tiers A 75+, B 62+, C 52+, and 2+ unverified families cap the tier at C.
    """
    unv = sorted(k for k in set(unverified) if k in WEIGHTS)
    pts = {k: (UNVERIFIED_SHARE * w if k in unv else max(0.0, min(float(families.get(k) or 0), w)))
           for k, w in WEIGHTS.items()}
    total = sum(pts.values())
    strong = sum(1 for k, w in WEIGHTS.items() if k not in unv and pts[k] >= STRONG_SHARE * w)
    notes = []
    if unv:
        notes.append(f"unverified (half weight): {', '.join(unv)}")
    if strong >= 5:
        total += 5
        notes.append("agreement bonus +5 (5+ families at 60%+ of weight)")
    elif strong < 3 and total > 60:
        total = 60
        notes.append("capped at 60 (fewer than 3 families at 60%+ of weight)")
    total = min(total, 100)
    if hard_flags:
        total = 0
        notes.append("hard red flag: score set to 0")
    elif soft_flags >= 3:
        total = 0
        notes.append("3+ soft red flags: reject")
    elif soft_flags:
        total -= SOFT_FLAG_COST * soft_flags
        notes.append(f"{soft_flags} soft red flag(s): -{SOFT_FLAG_COST * soft_flags}")
    total = round(max(0.0, min(total, 100)), 1)
    tier = tier_for(total, len(unv))
    if tier == "C" and len(unv) >= 2 and total >= TIERS[1][0]:
        notes.append("tier capped at C (2+ unverified families)")
    return {"score": total, "tier": tier, "families": pts, "strong_families": strong, "unverified": unv,
            "notes": notes}


RULE_LABELS = {"growth": "Growth (3y sales CAGR 15%+ or quarterly YoY 25%+)", "roce": "ROCE 15%+ (or 10%+ and rising 5 pts)",
               "cash_conversion": "Cash conversion (3y CFO / EBITDA 70%+)",
               "balance_sheet": "Balance sheet (net debt / EBITDA under 2, dilution under 10%)",
               "promoter": "Promoter (holding 40%+, pledge under 5%, not selling)",
               "valuation": "Valuation (P/E at most 1.5x growth)", "working_capital": "Working capital (debtor days not rising)"}
GATE_LABELS = {"mcap_range": "Market cap ₹500-20,000 cr", "liquidity": "Liquidity ₹1 cr+/day traded"}


def pass1_reasons(c: dict) -> list[str]:
    """Why a screened company did not pass, in funnel order (empty list when it passed)."""
    out = []
    for g, ok in (c.get("gates") or {}).items():
        if ok is False:
            out.append(f"fails {GATE_LABELS.get(g, g)}")
    known, met = c.get("known") or 0, c.get("met") or 0
    if known < 4:
        out.append(f"only {known} of 7 rules have data (needs 4)")
    else:
        need = max(4, math.ceil(0.7 * known))
        if met < need:
            out.append(f"met {met} of {known} rules (needs {need})")
    if c.get("blocked_by"):
        out.append(c["blocked_by"])
    return out
