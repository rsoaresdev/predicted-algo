# predicted-algo

An algorithmic trading bot for US ETFs (Python and Alpaca) and a private, read-only web dashboard for its performance history. The two apps run as separate processes, under separate Linux users and with separate database roles.

```
predicted-algo/
├── apps/
│   ├── bot/          # Python trading engine: strategies, risk, backtester, database
│   └── dashboard/    # Next.js read-only performance dashboard
├── infra/
│   ├── postgres/     # read-only role and schema
│   ├── docker-compose.dev.yml
│   ├── systemd/      # bot and dashboard services
│   └── reverse-proxy/{Caddyfile,nginx.conf}
└── .github/workflows/dashboard-ci.yml
```

## Why this structure

- **Two apps, no monorepo tooling.** There is one Python app and one JavaScript app, and they share no code, so Turborepo or Nx would add tooling for nothing. A flat `apps/` and `infra/` layout gives the boundaries that matter: separate processes, Linux users and database roles.
- **The bot runs from its own folder.** Its imports are relative to `apps/bot` (`from config import settings`), so it is started from there.

## The bot (`apps/bot`)

The strategies, risk rules and backtest results are in [`apps/bot/README.md`](apps/bot/README.md).

```bash
cd apps/bot
./.venv/bin/python -m unittest discover -s tests
./.venv/bin/python main.py
```

## The dashboard (`apps/dashboard`)

### Stack

| Concern | Choice | Notes |
| --- | --- | --- |
| Framework | Next.js 16 (App Router, Turbopack, React 19) | |
| Styling | Tailwind CSS v4 | Dark palette with semantic gain and loss colours |
| UI | shadcn/ui-style components on Radix | Hand-written, few dependencies |
| Charts | Recharts 3 | |
| Tables | TanStack Table 8 | Server-side filtering, sorting and pagination |
| Data | Drizzle ORM and postgres.js | Typed read-only queries, no migrations |
| Auth | Auth.js v5 (credentials and JWT), otplib TOTP, bcryptjs | Configured from the environment, no database writes |
| Validation | Zod 4 | Server Action inputs |

Auth.js fits a single hard-coded admin without user or session tables, which keeps the bot's database strictly read-only. Secrets live in the environment, and the TOTP secret is encrypted at rest with AES-256-GCM.

### Local development

Requires Node 20 or later and Postgres 18 (for the native `uuidv7()`).

Start Postgres with Docker, on port 5433:

```bash
docker compose -f infra/docker-compose.dev.yml up -d
```

Or use an existing local Postgres: create a separate development database, apply the schema and create the read-only role from `infra/postgres/*.sql`.

Then:

```bash
cd apps/dashboard
npm install
npm run seed          # SEED_DATABASE_URL must point at a role that can write
npm run setup:admin   # prints the AUTH_*, ADMIN_* and TOTP_* values and a QR code for .env.local
npm run dev           # http://localhost:3000, every page redirects to /login
```

`.env.example` documents every variable. `DATABASE_URL` must use the read-only `dashboard_ro` role.

### Production deploy

The dashboard is never built on the server.

1. **Build in CI** (`.github/workflows/dashboard-ci.yml`): typecheck, lint, `next build` in standalone mode, then upload `dashboard-standalone.tgz`.
2. **Copy the artifact** to the server and unpack it into `/opt/dashboard` (`server.js`, `.next/`, `node_modules/`, `public/`).
3. **Run it with systemd** (`infra/systemd/dashboard.service`) as the dedicated `dashboard` user, bound to localhost and hardened, with its environment in `/etc/dashboard/dashboard.env` (mode 0600). It has no access to the bot's `.env`.
4. **Put a reverse proxy in front**: Caddy with automatic TLS, or Nginx with certbot (`infra/reverse-proxy/`). Postgres stays on localhost and is never exposed.

**Without a domain of your own:** the app does not depend on the host name (`trustHost: true`), but HTTPS is required because the session cookie uses the `__Secure-` prefix, and Let's Encrypt does not issue certificates for a bare IP. Use a free host name such as `<ip-with-dashes>.sslip.io` (no sign-up) or `yourname.duckdns.org`, point the Caddyfile and `AUTH_URL` at it, and Caddy fetches the certificate. Open only ports 80, 443 and 22 in the firewall. A self-signed certificate with `tls internal` also works, with a browser warning (see the Caddyfile).

The full server setup is in [`infra/DEPLOY_HETZNER.md`](infra/DEPLOY_HETZNER.md).

## Security model

- **Read-only database role.** `dashboard_ro` can only `SELECT`; writes are revoked in the database (`infra/postgres/01_dashboard_readonly_role.sql`).
- **One login gate for every page.** `proxy.ts` (the Next.js 16 middleware) redirects every route except `/login` and `/api/auth/*` to the login page, and each Server Action checks the session again.
- **Two-factor sign-in.** A bcrypt password and a TOTP code are both required. The session is an `httpOnly`, `secure`, `sameSite=strict` JWT cookie, and there is no public sign-up.
- **Process isolation.** The dashboard runs as its own Linux user, with no Alpaca credentials and no write access to the database.

## Metric parity

Sharpe ratio, maximum drawdown, profit factor, CAGR and win rate are computed in TypeScript (`apps/dashboard/src/lib/metrics.ts`) with the same formulas as `apps/bot/backtester.py`: sample standard deviation, `√252` annualisation, `equity / cummax − 1`, and gross profit over gross loss. The equity curve comes from `daily_metrics.ending_equity`, and every trade-level figure is recomputed from the raw `trades` rows.

## License

[MIT](LICENSE)
