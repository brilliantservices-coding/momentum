# Weekly momentum strategy (India): design

This folder runs the *Weekly Momentum Strategy for Indian Equities* on Google Cloud. A job reviews
the market every Saturday and proposes changes to the **Momentum** book. You review them in a
portal and sign off, and you place the orders yourself in Kite. A separate Zerodha link (a relay on
a small VM with a static IP) currently does only a read-only connection check and a
place-and-cancel test order. It never trades for you. Educational, not investment advice.

For day-to-day operations (deploys, secrets, the daily Kite login, troubleshooting, costs), see
[OPERATIONS.md](OPERATIONS.md).

## The pieces

```
 Cloud Scheduler (Sat 08:00 IST)
        │ POST :run
        ▼
 Cloud Run job  mom-weekly ──► NSE (Nifty 500 list) + Yahoo Finance (2y daily prices, LIQUIDBEES)
        │ writes
        ▼
 Firestore (default DB, mom_* collections) ◄──── Cloud Run service mom-portal (behind IAP)
                                                     │  private VPC route (Direct VPC egress)
                                                     ▼
                                       mom-kite-vm (e2-micro, Mumbai, static IP 34.93.168.72)
                                         └─ Kite relay :8080 (internal only) ──► api.kite.trade
```

| Piece | Where | What it does |
|---|---|---|
| `mom/weekly.py` | Cloud Run job `mom-weekly` | Downloads data, computes signals, writes a *pending* week |
| `mom/signals.py` | (library) | Pure indicator maths: MAs, RSI, RS vs Nifty, RS rating, momentum score, regime |
| `mom/strategy.py` | (library) | The weekly routine: filters, ranking, exits, sizing, LIQUIDBEES parking, funnel, summary |
| `mom/book.py` | (library) | The Momentum book and sign-off: applies approved changes, writes the trade journal |
| `mom/store.py` | (library) | Firestore in GCP, JSON files locally; same interface |
| `portal/` | Cloud Run service `mom-portal` | Review, sign-off, history, book, screen, settings, Zerodha page |
| `mom/kite.py` | inside the portal | Thin client for the relay |
| `relay/kite_relay.py` | systemd service on `mom-kite-vm` | The only code that talks to Kite |

## The weekly run, step by step

The doc's routine is: regime first, then exits, then new buys.

1. **Data.** Fetch the Nifty 500 list from NSE (cached in `mom_universe/current` in case NSE is
   down). Then fetch two years of adjusted daily OHLCV from Yahoo (`SYMBOL.NS`) in chunks of 80,
   retrying any symbol lost to errors, plus `^NSEI`, `^CRSLDX` (Nifty 500) and `LIQUIDBEES.NS`.
   LIQUIDBEES is priced but never screened.
2. **Regime** (`signals.regime`). This uses the Nifty 500 weekly close against its 40-week MA, and
   *breadth*, the % of stocks above their 200 DMA.
   - Green: the index is above a rising 40-week MA and breadth is above 50%. Up to 100% in stocks.
   - Red: the index is below a falling 40-week MA, or breadth is under 35%. No new buys, and at
     most 20% in stocks.
   - Amber: anything else. Up to 55% in stocks, and only the top 5 are bought, at half size.
   - Downgrades act at once. An upgrade moves one level, and only after two weekly closes in the
     better regime.
3. **Filters** (`strategy.rank_universe`). Each stock gets one pass/fail per factor: price history,
   ₹10 cr/day turnover, the 8 Minervini Trend Template conditions, weekly RSI > 60, daily RSI > 50,
   RS 55 > 0, RS 123 > 0, not on your exclusion list, and the optional Screener quality filter.
4. **Ranking.** Stocks passing every filter are ranked by the volatility-adjusted 6- and 12-month
   return, skipping the latest month (the NSE momentum index method).
5. **Exits** for every holding. A holding is sold on any of these: its stop is hit, a weekly close
   below the 30-week MA (or that MA turns down), RS 55 below 0 two weeks running with price under
   the 10-week MA, weekly RSI under 40, its rank falls below 40, it's on your exclusion list, or
   (after a +10% move) a weekly close under the 10-week MA. Stops only move up: to breakeven after
   +10%, then to the 3-week swing low. A one-third trim is proposed after a fast 20% gain.
6. **Regime cap.** If holdings exceed the regime's exposure cap, the weakest-ranked are sold.
7. **Buys.** Candidates are the top 15 (top 5 in Amber, none in Red), and each needs a valid entry
   this week. That means a breakout above the 10-week base on 1.5x volume, or a quiet pullback to a
   rising 10-week MA, and no more than 5% above the pivot. Sizing:
   - 1% of the book at risk, halved in Amber or after a 10% drawdown.
   - Stop under the 15-day low, never more than 8% away.
   - Caps: 10% per stock, 25% per sector, 6% total open risk, and 15 positions.
8. **Idle cash.** Whatever isn't needed goes into LIQUIDBEES as a `park` change, keeping a 1% cash
   buffer. Units are sold (`unpark`) to fund buys.
9. **Output.**
   - `mom_weeks/{week}`: regime, changes with reasons, holds, watchlist, ranking, funnel, and a
     plain-English summary.
   - `mom_screens/{week}`: every stock's factor bits and key numbers.
   - `mom_perf/{week}`: book value against Nifty.
   - A previous week that was still pending is marked *superseded*.

The week id is the Friday the run reviews (for example `2026-11-06`). A signed-off week is never
recomputed unless forced.

## Sign-off and the Momentum book

Kite has no named sub-holdings, so the strategy keeps its own book in `mom_portfolio/Momentum`:
cash, positions (qty, entry, stop, entry date, sector, highest close) and peak value. On the week
page you untick anything you don't want, edit quantities and prices to your actual fills, and sign
off. `book.sign_off` applies exactly what you signed and writes each change to `mom_journal`.
Orders you place by hand in Kite web or the app cannot carry a tag, so this book is the record of the strategy's trades. Orders placed through the API (today only the test order) carry the tag `Momentum`.

## Zerodha link

- **Why a VM:** SEBI's rules (from April 2026) reject API orders that don't come from a registered
  static IP. Cloud Run has no fixed outbound IP, so all Kite traffic goes through `mom-kite-vm`,
  whose IP 34.93.168.72 is whitelisted on the Kite developer profile.
- **Kite app:** "Momentum", on the free Personal plan. That covers orders, GTT and portfolio, with
  no market data, which is fine because prices come from Yahoo.
- **Relay security:**
  - It listens only on the VM's internal IP, and the firewall rule `mom-allow-relay` opens port
    8080 to the asia-south1 subnet only.
  - Each request must carry the shared `mom-relay-token` (Secret Manager).
  - The Kite API secret is in Secret Manager (`mom-kite-api-secret`) and is read only by the VM.
  - The day's access token is stored only on the VM (`/var/lib/mom-relay/session.json`, mode 600)
    and expires at 06:00 IST.
- **Login:** Portal "Connect Zerodha" → Kite login page → Kite redirects to
  `/kite/callback?request_token=…` → the relay exchanges it for an access token.
- **What it can do:** `/check` reads profile, funds and holdings. `/test-order` places 1
  LIQUIDBEES limit BUY at 3% below the last price (it cannot fill), tagged Momentum, then cancels
  it. There is no endpoint that places real strategy orders. Adding one needs your explicit
  decision.

## Shared with the multibagger app

Both apps run in GCP project `arched-tape-447611-r0` (asia-south1) and share the (default) Firestore
database. Multibagger uses unprefixed collections; this app uses only `mom_*`. If multibagger has a
Screener fundamentals upload, this app's quality filter reads that `fundamentals` collection. One
IAP OAuth client is applied project-wide, so both portals use Google sign-in. Every momentum
resource is named `mom-*` and labelled `app=momentum`, and the `mom-monthly-…` budget counts only
that label.

## Firestore collections

| Collection | Doc id | Contents |
|---|---|---|
| `mom_weeks` | week (Friday date) | proposal, status `pending` / `signed_off` / `superseded`, decisions |
| `mom_screens` | week | factor list and per-stock rows for the Screen page |
| `mom_portfolio` | `Momentum` | the book |
| `mom_journal` | `{week}-{change}-{symbol}` | signed-off changes (trade log) |
| `mom_perf` | week | book value, Nifty 500, Nifty 50 |
| `mom_runs` | Cloud Run execution id | status, summary, log tail |
| `mom_settings` | `current` | parameters edited in the portal |
| `mom_universe` | `current` | last good Nifty 500 list |
| `mom_kite` | `last` | last connection check and test-order result (no tokens) |
| `mom_audit` | timestamped | runs started, Kite logins and test orders from the portal |

## Develop locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r relay/requirements.txt pytest httpx
python -m pytest -q                       # synthetic market data and mocked Kite; no network
uvicorn portal.app:app --reload           # MOM_BACKEND=local keeps data in .localdata/
python -m mom.weekly --force              # a real run (needs internet for NSE and Yahoo)
```
