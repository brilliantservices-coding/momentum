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
