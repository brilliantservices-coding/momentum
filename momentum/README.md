# Weekly momentum strategy (India)

Implements the *Weekly Momentum Strategy for Indian Equities* doc as a weekly job on Google Cloud,
plus a portal where the owner reviews each week's proposed changes and signs off. Signed-off changes
update the **Momentum** book (this strategy's own holdings, stops and trade journal). Nothing here
places orders: you place them in Kite yourself, tagged `Momentum`. Educational, not advice.

## What the weekly run does (Saturday 08:00 IST, on Friday's close)

1. **Universe:** Nifty 500 constituents from NSE; two years of daily prices from Yahoo Finance.
2. **Regime:** Green / Amber / Red from Nifty 500 vs its rising 40-week MA and the % of stocks above
   their 200 DMA. Upgrades need two weekly closes; the regime caps money in stocks at 100 / 55 / 20%.
3. **Exits** for every holding: stop hit, weekly close under the 30-week MA or that MA turning down,
   RS 55 below 0 two weeks running with price under the 10-week MA, weekly RSI under 40, rank below
   40, your exclusion list, or (after +10%) a weekly close under the 10-week MA. Stops are trailed up
   (breakeven after +10%, then the 3-week swing low); a one-third trim is proposed after a fast 20% run.
4. **Buys:** Minervini Trend Template, RS rating 70+, weekly RSI > 60, daily RSI > 50, RS 55 and
   RS 123 above 0, ₹10 cr+ daily turnover, optional Screener quality filter, then ranked by the
   volatility-adjusted 6/12-month score. Only the top 15 (top 5 in Amber, none in Red) are eligible,
   and only with a valid pivot this week (breakout on 1.5x volume, or a quiet pullback to the rising
   10-week MA); others go on the watchlist.
5. **Sizing:** 1% of the book at risk per position (halved after a 10% drawdown or in Amber), stop
   under the 15-day low and never more than 8% away, 10% per stock, 25% per sector, 6% total heat.

The result is saved as a *pending* week. The portal shows it with the reason for every change; you
untick what you don't want, edit quantity and price to your actual fills, and sign off.

## Quality filter

If a Screener.in export has been uploaded in the multibagger portal (same Firestore database), the
run applies: market cap > ₹1,000 cr, 3-year sales growth > 12%, profit growth > 15%, pledge < 5%,
positive operating cash flow, and for non-financials ROCE > 15% and debt/equity < 1. Stocks without
fundamentals are not blocked; the change says "quality not checked".

## Deploy

```bash
cp infra/config.example.env infra/config.env   # project, region, budget, your email(s)
bash infra/deploy.sh                           # Cloud Shell, macOS/Linux or Git Bash; re-runnable
gcloud run jobs execute mom-weekly --region asia-south1 --wait   # first run
```

Creates (all `mom-*`, label `app=momentum`): Cloud Run job `mom-weekly` + Cloud Scheduler trigger,
Cloud Run service `mom-portal` behind IAP, three service accounts, an Artifact Registry repo that
keeps 3 images, and a budget alert on `app=momentum` spend. The Zerodha VM `mom-kite-vm` (static IP
`mom-kite-ip`) is separate and carries the same label.

## Develop locally

```bash
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt pytest httpx
python -m pytest -q                       # synthetic market data, no network
uvicorn portal.app:app --reload           # MOM_BACKEND=local keeps data in .localdata/
python -m mom.weekly --force              # a real run (needs internet for NSE and Yahoo)
```
