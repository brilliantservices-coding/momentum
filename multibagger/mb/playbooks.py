"""Sector playbooks (strategy v2, "Sector playbooks"): which facts matter for each business model.

A playbook decides what evidence the analyst looks for and which failure modes the red team tests; it
never qualifies a stock by itself. Classification uses Yahoo/Trendlyne sector and industry text; a company
gets a primary playbook and, where the text fits more than one, secondary ones.
"""
from __future__ import annotations

VERSION = "v2-2026-11-08"

PLAYBOOKS = {
    "manufacturing": {
        "name": "Manufacturing / EMS",
        "words": ("electronic", "electrical equipment", "manufactur", "industrial", "machinery", "metal fabrication",
                  "packaging", "building products", "tools", "textile", "plastic", "paper", "furnishing"),
        "signals": ["Customer wins or orders", "Demand-backed capex", "Capacity commissioned on time",
                    "Utilisation ramp", "Rising asset turns / incremental ROCE"],
        "disqualifiers": ["Speculative capex", "Inventory build-up", "One-customer dependence", "Negative incremental ROCE"],
    },
    "chemicals": {
        "name": "Specialty chemicals",
        "words": ("chemical", "agricultural inputs", "fertili", "agrochem", "pigment", "dyes"),
        "signals": ["Customer qualification", "Niche market share", "Debottlenecking / backward integration",
                    "Utilisation", "Margin resilience through raw-material cycles"],
        "disqualifiers": ["Commodity-price windfall", "Global overcapacity", "Customer concentration"],
    },
    "capital_goods": {
        "name": "Capital goods / Defence / Railways",
        "words": ("aerospace", "defense", "defence", "railroad", "railway", "engineering & construction",
                  "construction", "specialty industrial machinery", "farm & heavy", "infrastructure"),
        "signals": ["Order inflow", "Executable backlog (book-to-bill)", "Capacity to execute",
                    "Milestone conversion to revenue", "Collections / receivables"],
        "disqualifiers": ["Unfunded orders", "Weak order margins", "Cancellations", "Receivable stress"],
    },
    "lender": {
        "name": "Banks / NBFCs",
        "words": ("bank", "credit services", "nbfc", "financ", "lending", "mortgage", "housing finance"),
        "signals": ["Risk-adjusted loan growth", "Vintage quality (Stage 2/3, GNPA/NNPA)", "Stable credit cost",
                    "Diversified liabilities", "Sustainable ROA and capital adequacy"],
        "disqualifiers": ["Rapid unseasoned growth", "Liability mismatch", "Sector or borrower concentration",
                          "Recognition risk (low reported NPA with fast growth)"],
    },
    "consumer": {
        "name": "Consumer brands",
        "words": ("consumer", "household", "personal products", "packaged foods", "beverage", "apparel",
                  "footwear", "confectioner", "tobacco", "leisure", "luxury", "home improvement"),
        "signals": ["Volume share gain", "Premiumisation", "Distribution productivity", "Repeat demand",
                    "Cash conversion"],
        "disqualifiers": ["Channel stuffing", "Discount-led growth", "Advertising without repeat purchase"],
    },
    "retail": {
        "name": "Retail / QSR",
        "words": ("retail", "restaurant", "department store", "specialty retail", "grocery"),
        "signals": ["Same-store growth", "Improving store cohorts", "Attractive store payback", "Disciplined openings"],
        "disqualifiers": ["Growth only from openings", "Weakening existing stores", "Excessive lease commitments"],
    },
    "pharma": {
        "name": "Pharma / CDMO",
        "words": ("drug", "pharma", "biotech", "medical", "health", "diagnostic"),
        "signals": ["Approvals / qualifications", "Launches or contracts", "Plant utilisation", "Mix improvement",
                    "Reinvestment"],
        "disqualifiers": ["Regulatory observations", "Price erosion", "Binary pipeline dependence"],
    },
    "auto": {
        "name": "Auto / ancillaries / EV",
        "words": ("auto", "vehicle", "tyre", "tire", "motorcycle", "auto parts", "recreational vehicles"),
        "signals": ["Platform nominations", "Start of production dates", "Content per vehicle", "OEM volume ramp",
                    "Customer diversification"],
        "disqualifiers": ["OEM concentration", "Delayed platforms", "Technology substitution"],
    },
    "it_services": {
        "name": "IT services / ER&D",
        "words": ("information technology services", "it services", "consulting", "engineering services"),
        "signals": ["Deal wins", "Deal conversion to revenue", "Utilisation and pricing", "Client mining",
                    "Stable attrition"],
        "disqualifiers": ["Poor conversion", "Uncontrolled attrition", "Pricing erosion"],
    },
    "software": {
        "name": "SaaS / software products",
        "words": ("software", "saas", "internet content", "application"),
        "signals": ["ARR growth", "Net revenue retention", "Efficient customer acquisition", "Expansion revenue",
                    "Free-cash-flow progression"],
        "disqualifiers": ["High churn", "Growth bought through uneconomic acquisition"],
    },
    "hospitals": {
        "name": "Hospitals / diagnostics",
        "words": ("hospital", "medical care", "diagnostics & research", "healthcare facilities"),
        "signals": ["Occupancy / test volumes", "ARPOB / realisation", "Case mix", "Mature-unit margins",
                    "Disciplined expansion"],
        "disqualifiers": ["Debt-funded expansion", "Weak new-unit economics"],
    },
    "real_estate": {
        "name": "Real estate",
        "words": ("real estate", "reit", "property", "residential construction"),
        "signals": ["Presales", "Collections", "Construction progress", "Cash conversion", "Deleveraging"],
        "disqualifiers": ["Bookings not converting to cash", "Aggressive land buying", "Leverage"],
    },
    "power": {
        "name": "Power / renewables",
        "words": ("utilities", "power", "renewable", "solar", "electric utilit", "independent power"),
        "signals": ["PPA / contract signed", "Funding tied up", "Commissioning (COD)", "Generation / utilisation",
                    "Cash returns"],
        "disqualifiers": ["Weak DISCOM / counterparty", "Project delays", "Poor project IRR"],
    },
    "capital_markets": {
        "name": "Capital-market infrastructure",
        "words": ("capital markets", "asset management", "financial data", "stock exchange", "insurance brokers",
                  "broker"),
        "signals": ["Participant / account growth", "Market share", "Activity levels", "Operating leverage",
                    "Recurring monetisation"],
        "disqualifiers": ["Pure bull-market volume", "Regulatory concentration"],
    },
    "telecom_digital": {
        "name": "Telecom / digital infrastructure",
        "words": ("telecom", "communication equipment", "data center", "data centre"),
        "signals": ["Contracted demand", "Utilisation", "Tenancy / traffic growth", "Disciplined capex",
                    "Recurring cash flow"],
        "disqualifiers": ["Debt-funded build-out without contracted demand"],
    },
    "cyclical": {
        "name": "Cyclicals (metals, materials, energy)",
        "words": ("steel", "aluminum", "aluminium", "copper", "mining", "coal", "oil", "gas", "cement",
                  "building materials", "graphite", "commodity", "marine shipping", "sugar", "lumber"),
        "signals": ["Normalised (mid-cycle) margins", "Cost position", "Capacity additions vs industry supply",
                    "Utilisation", "Volume vs realisation split"],
        "disqualifiers": ["Peak-cycle earnings", "New global capacity", "Price-only growth"],
    },
    "general": {
        "name": "General (no specific playbook)",
        "words": (),
        "signals": ["Demand or share acceleration", "Improving incremental economics", "Reinvestment runway",
                    "Competitive reinforcement"],
        "disqualifiers": ["Growth without cash conversion"],
    },
}
# Lenders and cyclicals need their own metrics, so they are checked first when text matches several.
ORDER = ["lender", "capital_markets", "cyclical", "hospitals", "pharma", "it_services", "software", "auto",
         "chemicals", "capital_goods", "power", "telecom_digital", "real_estate", "retail", "consumer",
         "manufacturing"]


def classify(m: dict) -> dict:
    text = f"{m.get('sector') or ''} {m.get('industry') or ''}".lower()
    hits = [p for p in ORDER if any(w in text for w in PLAYBOOKS[p]["words"])]
    primary = hits[0] if hits else "general"
    return {"playbook": primary, "secondary_playbooks": hits[1:3]}


def brief(playbook_id: str) -> dict:
    p = PLAYBOOKS.get(playbook_id) or PLAYBOOKS["general"]
    return {"id": playbook_id, "name": p["name"], "critical_signals": p["signals"], "disqualifiers": p["disqualifiers"]}


# Conviction family weights per playbook (strategy v2: "each sector has its own leading indicators").
# Every profile sums to 100. Lenders and real estate weigh governance and balance-sheet quality more,
# order-book businesses weigh visibility, cyclicals weigh the capital cycle and valuation.
DEFAULT_WEIGHTS = {"theme": 15, "financial": 20, "management": 20, "visibility": 15, "ecosystem": 10,
                   "valuation": 10, "governance": 10}
WEIGHT_PROFILES = {
    "lender": {"theme": 10, "financial": 25, "management": 20, "visibility": 10, "ecosystem": 5, "valuation": 10,
               "governance": 20},
    "cyclical": {"theme": 20, "financial": 15, "management": 15, "visibility": 10, "ecosystem": 10, "valuation": 20,
                 "governance": 10},
    "capital_goods": {"theme": 15, "financial": 15, "management": 15, "visibility": 25, "ecosystem": 10,
                      "valuation": 10, "governance": 10},
    "consumer": {"theme": 10, "financial": 25, "management": 20, "visibility": 15, "ecosystem": 10, "valuation": 10,
                 "governance": 10},
    "retail": {"theme": 10, "financial": 25, "management": 20, "visibility": 15, "ecosystem": 5, "valuation": 15,
               "governance": 10},
    "hospitals": {"theme": 10, "financial": 25, "management": 20, "visibility": 15, "ecosystem": 5, "valuation": 15,
                  "governance": 10},
    "software": {"theme": 10, "financial": 20, "management": 20, "visibility": 20, "ecosystem": 10, "valuation": 10,
                 "governance": 10},
    "it_services": {"theme": 10, "financial": 20, "management": 20, "visibility": 20, "ecosystem": 10,
                    "valuation": 10, "governance": 10},
    "pharma": {"theme": 10, "financial": 20, "management": 20, "visibility": 15, "ecosystem": 15, "valuation": 10,
               "governance": 10},
    "real_estate": {"theme": 10, "financial": 20, "management": 20, "visibility": 15, "ecosystem": 5,
                    "valuation": 10, "governance": 20},
    "capital_markets": {"theme": 20, "financial": 20, "management": 15, "visibility": 10, "ecosystem": 10,
                        "valuation": 15, "governance": 10},
}
for _p in ("manufacturing", "chemicals", "auto", "power", "telecom_digital"):
    WEIGHT_PROFILES[_p] = {"theme": 15, "financial": 20, "management": 15, "visibility": 20, "ecosystem": 10,
                           "valuation": 10, "governance": 10}


def weights_for(playbook_id: str | None) -> dict:
    return dict(WEIGHT_PROFILES.get(playbook_id or "", DEFAULT_WEIGHTS))


# Sector-critical data: (label, fundamentals field or None, fact types that supply it). A field-backed item
# is missing when the field is empty; a document-backed item is missing until extraction finds a fact.
CRITICAL_DATA = {
    "lender": [("Gross / net NPA", None, ("asset_quality",)), ("Credit cost", None, ("asset_quality",)),
               ("Capital adequacy (CAR)", None, ("capital_adequacy",)), ("Loan (AUM) growth", None, ("loan_growth",)),
               ("Return on assets", None, ("return_on_assets",))],
    "capital_goods": [("Order book to revenue", None, ("order_book",)), ("Order inflow", None, ("order_win",)),
                      ("Receivable days", "debtor_days", ()), ("Executable capacity", None, ("capacity",))],
    "manufacturing": [("Capacity utilisation", None, ("utilisation",)), ("Capex plan", None, ("capex",)),
                      ("Incremental ROCE", "incremental_roce", ()), ("Customer wins", None, ("customer", "order_win"))],
    "chemicals": [("Capacity utilisation", None, ("utilisation",)), ("Capex plan", None, ("capex",)),
                  ("Margin through the cycle", "ebitda_margin", ()), ("Customer qualification", None, ("customer",))],
    "consumer": [("Volume growth", None, ("volume_growth",)), ("Distribution reach", None, ("distribution",)),
                 ("Cash conversion", "cfo_3y", ())],
    "retail": [("Same-store growth", None, ("same_store_growth",)), ("Store additions", None, ("capacity", "guidance"))],
    "pharma": [("Regulatory status (USFDA etc.)", None, ("regulatory",)), ("Approvals and launches", None, ("product",)),
               ("Plant utilisation", None, ("utilisation",))],
    "auto": [("Platform wins / content per vehicle", None, ("customer", "order_win")), ("Capacity", None, ("capacity",))],
    "it_services": [("Deal wins (TCV)", None, ("order_win",)), ("Client concentration", None, ("customer",))],
    "software": [("ARR / recurring revenue", None, ("recurring_revenue",)), ("Retention", None, ("retention",))],
    "hospitals": [("Occupancy", None, ("utilisation",)), ("ARPOB", None, ("realisation",)), ("Bed additions", None, ("capacity",))],
    "real_estate": [("Presales", None, ("presales",)), ("Collections", None, ("collections",)),
                    ("Net debt", "debt", ())],
    "power": [("Contracted capacity (PPA)", None, ("order_win", "capacity")), ("Commissioning dates", None, ("capacity",))],
    "capital_markets": [("Active clients / market share", None, ("market_share",))],
    "telecom_digital": [("Contracted demand", None, ("order_win", "customer")), ("Utilisation", None, ("utilisation",))],
    "cyclical": [("Mid-cycle ROCE", "roce_3y_avg", ()), ("Capacity additions", None, ("capacity", "capex")),
                 ("Cost position", None, ("cost_position",))],
}


def critical_missing(playbook_id: str | None, m: dict, fact_types=None) -> list[str]:
    """Sector-critical data not yet on file. Without fact_types (the screen), document-backed items are
    left out: they are checked on the stock page and by the analyst once extraction has run."""
    out = []
    for label, field, types in CRITICAL_DATA.get(playbook_id or "", []):
        if field:
            if m.get(field) is None:
                out.append(label)
        elif fact_types is not None and not set(types) & set(fact_types):
            out.append(label)
    return out
