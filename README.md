# momentum

Stock research and trading tools for Indian equities (NSE/BSE), run on Google Cloud. Each app lives
in its own folder and can be deployed on its own. Educational tools, not investment advice.

| App | Folder | What it does | Design | Operations |
|---|---|---|---|---|
| Weekly momentum strategy | [`momentum/`](momentum/) | Saturday review of the Nifty 500 (regime, trend template, RS, ranking), weekly sign-off portal, the Momentum book, LIQUIDBEES parking, and the Zerodha link through a static-IP VM | [README](momentum/README.md) | [OPERATIONS](momentum/OPERATIONS.md) |
| Multibagger agents | [`multibagger/`](multibagger/) | Agents that build the universe, ingest filings and fundamentals, screen and flag red flags, plus a control portal | [README](multibagger/README.md) | [OPERATIONS](multibagger/OPERATIONS.md) |

## Shared infrastructure

- **GCP project** `arched-tape-447611-r0`, region `asia-south1` (Mumbai).
- **Firestore:** both apps share the (default) database. Multibagger uses unprefixed collections
  (`agents`, `runs`, `fundamentals`, …), and momentum uses only `mom_*` collections. Momentum reads
  multibagger's `fundamentals` for its optional quality filter.
- **Sign-in:** both portals (`mb-portal`, `mom-portal`) sit behind IAP with Google sign-in. One
  OAuth client is applied project-wide (the project has no Google Cloud organization, so IAP needs
  a custom client).
- **Naming and budgets:** resources are named `mb-*` / `mom-*` and labelled `app=multibagger` /
  `app=momentum`. Each app has its own billing budget filtered on its label, so neither app's spend
  counts against the other, and other apps in the project are excluded.
- **Deploys:** each app has `infra/deploy.sh` (idempotent) and an `infra/config.env` that is never
  committed. This repository is public, so no secrets go in it.
