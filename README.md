# predicted-algo — monorepo

Algorithmic trading bot (Python/Alpaca) **and** a private, read-only web
dashboard for its performance history. Two apps, clear boundaries, separate OS
users and database roles.

```
predicted-algo/
├── apps/
│   ├── bot/          # the existing Python bot (engine, risk, strategies,
│   │                 #   backtester, database/*) — moved here unchanged
│   └── dashboard/    # Next.js 16 read-only dashboard (this addition)
├── infra/
│   ├── postgres/     # 01 read-only role · 02 schema · (seed lives in dashboard)
│   ├── docker-compose.dev.yml
│   ├── systemd/dashboard.service
│   └── reverse-proxy/{Caddyfile,nginx.conf}
└── .github/workflows/dashboard-ci.yml
```

## Why this structure

- **Two top-level apps, no Turborepo/Nx.** There is exactly one JS app and one
  Python app, and they share **no** JS code. A JS monorepo orchestrator would add
  tooling for zero benefit. A flat `apps/` + `infra/` layout gives the boundaries
  that actually matter: separate processes, separate Linux users, separate DB
  roles.
- **The bot moved verbatim into `apps/bot`.** Its imports are root-relative
  (`from config import settings`), so running from `apps/bot/` keeps everything
  working — no code changes. The production copy on the VPS is unaffected (it's a
  different machine). A pre-move backup tarball was created next to the repo.
- The repo is **not** under git. If you want history/undo going forward:
  `git init && git add -A && git commit -m "monorepo baseline"`.

## The bot (`apps/bot`) — unchanged

```bash
cd apps/bot
./.venv/bin/python -m unittest discover -s tests   # 28 tests pass
./.venv/bin/python main.py
```

## The dashboard (`apps/dashboard`)

### Stack (current stable, June 2026)

| Concern | Choice | Notes |
|---|---|---|
| Framework | **Next.js 16.2** (App Router, Turbopack, React 19) | latest stable |
| Styling | **Tailwind CSS v4** | intentional dark palette, semantic gain/loss |
| UI | **shadcn/ui**-style components (Radix) | hand-authored, lean deps |
| Charts | **Recharts 3** | single charting layer |
| Tables | **TanStack Table 8** | server-driven filter/sort/pagination |
| Data | **Drizzle ORM 0.45** + postgres.js | read-only typed SELECTs, no migrations |
| Auth | **Auth.js v5** (Credentials + JWT) + **otplib** TOTP + **bcryptjs** | env-based, zero DB writes |
| Validation | **zod 4** | Server Action inputs |

Auth.js was chosen over Better Auth because the spec maps to it 1:1 and a single
hard-coded admin needs no user/session tables — keeping the bot DB strictly
read-only. Secrets live in env, the TOTP secret AES-256-GCM-encrypted at rest.

### Local development

Requires Node 20+ and a Postgres 18 (native `uuidv7()`).

```bash
# Option A — Docker (portable)
docker compose -f infra/docker-compose.dev.yml up -d   # Postgres on :5433

# Option B — an existing local Postgres: create an isolated dev DB, apply schema,
# create the read-only role (see infra/postgres/*.sql).

cd apps/dashboard
npm install
npm run seed          # SEED_DATABASE_URL must point at a WRITABLE role
npm run setup:admin   # prints AUTH_*/ADMIN_*/TOTP_* + a QR — paste into .env.local
npm run dev           # http://localhost:3000  (everything redirects to /login)
```

`.env.local` (dev) / `.env.example` (template) document every variable.
`DATABASE_URL` must use the **read-only** `dashboard_ro` role.

### Production deploy (never build on the VPS)

1. **Build in CI** (`.github/workflows/dashboard-ci.yml`): typecheck → lint →
   `next build` (standalone) → upload `dashboard-standalone.tgz`.
2. **Ship the artifact** to the VPS and unpack into `/opt/dashboard`
   (`server.js`, `.next/`, `node_modules/`, `public/`).
3. **Run via systemd** (`infra/systemd/dashboard.service`) as the dedicated
   `dashboard` user — localhost-bound, hardened, env from
   `/etc/dashboard/dashboard.env` (0600). No access to the bot's `.env`.
4. **Reverse proxy** with Caddy (auto-TLS) or Nginx + certbot
   (`infra/reverse-proxy/`). Postgres stays on localhost — never exposed.

**Hosting on Hetzner Cloud without a personal domain:** no app code changes are
needed (the app is host-agnostic, `trustHost: true`). HTTPS is required (the
session cookie is `__Secure-`), and Let's Encrypt won't issue for a bare IP, so
use a **free hostname**: `<ip-with-dashes>.sslip.io` (instant, zero signup) or a
free `yourname.duckdns.org`. Point the Caddyfile site address + `AUTH_URL` at it
and Caddy fetches the cert automatically. Open only ports 80/443 (+22) in the
Hetzner Cloud Firewall; leave Postgres on localhost. (Bare-IP self-signed TLS via
`tls internal` also works but shows a browser warning — see the Caddyfile.)

## Security model

- **Read-only DB role** `dashboard_ro` — `SELECT` only, writes revoked at the DB
  level (`infra/postgres/01_dashboard_readonly_role.sql`).
- **Global auth gate** — `proxy.ts` (Next 16's renamed middleware) redirects every
  route except `/login` (and `/api/auth/*`) to login; each Server Action re-checks
  the session.
- **2FA** — password (bcrypt) **and** TOTP required; session is a `httpOnly`,
  `secure`, `sameSite=strict` JWT cookie. No public registration.
- **Process isolation** — dashboard runs as its own Linux user with no Alpaca
  credentials and no DB write access.

## Metric parity

Sharpe, max drawdown, profit factor, CAGR, win rate are computed in TypeScript
(`apps/dashboard/src/lib/metrics.ts`) with the **same formulas** as
`apps/bot/backtester.py` (sample std, `√252` annualization, `equity/cummax−1`,
gross-profit/gross-loss). The equity curve comes from `daily_metrics.ending_equity`;
all trade-level aggregates are recomputed from raw `trades` rows.
