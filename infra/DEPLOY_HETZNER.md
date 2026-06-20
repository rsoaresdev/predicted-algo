# Deploying predicted-algo to Hetzner Cloud + a 2-month paper trade

End-to-end, copy-pasteable guide to run the **bot** and the **dashboard** on one
Hetzner Cloud VPS, on the same Postgres, with TLS — then run a **2-month paper
trade** before risking real money.

Architecture on one box:

```
                    ┌──────────────── Hetzner VPS (Ubuntu 24.04, UTC) ──────────────┐
   you ── https ──▶ │  Caddy :443  ──▶  dashboard (node) :3000  ──┐                  │
                    │                                             │ reads (RO role)  │
                    │  bot (python)  ──writes──▶  PostgreSQL  ◀────┘                  │
                    │      └── outbound https ──▶ Alpaca paper API                    │
                    └───────────────────────────────────────────────────────────────┘
        Postgres + node bind to 127.0.0.1 only. Only 22 (SSH) + 443 (HTTPS) are open.
```

Three Linux users, three trust levels: **`bot`** (Alpaca keys, read-write DB),
**`dashboard`** (no Alpaca keys, read-only DB), **`postgres`**. Compromising the
public-facing dashboard never exposes trading keys or write access.

> Commands assume Ubuntu 24.04. Run as a sudo-capable non-root user unless a step
> says otherwise. Replace `SERVER_IP`, passwords, and keys with your own.

---

## 0. Create the server

1. Hetzner Cloud console → **New project** → **Add server**.
   - Location: closest to you (e.g. Falkenstein/Nuremberg EU, or Ashburn US).
   - Image: **Ubuntu 24.04**.
   - Type: **CX22** (2 vCPU / 4 GB) is plenty for bot + dashboard + Postgres.
     CX32 if you want headroom.
   - **SSH key**: add your public key (don't use password auth).
   - Create. Note the **public IPv4** (`SERVER_IP`).

2. (Recommended) In the Hetzner **Firewall** product, attach a firewall allowing
   inbound only **TCP 22** and **TCP 443** (and ICMP). We also set up `ufw`
   on-host as defense in depth.

---

## 1. First login & host hardening

```bash
ssh root@SERVER_IP

# Create your admin user (skip if Hetzner already made one)
adduser deploy
usermod -aG sudo deploy
rsync --archive --chown=deploy:deploy ~/.ssh /home/deploy   # copy your key
# log out, then back in as: ssh deploy@SERVER_IP
```

```bash
# Everything below runs as `deploy` with sudo.
sudo timedatectl set-timezone UTC          # P1: the bot buckets daily metrics in UTC
sudo apt update && sudo apt -y upgrade

# Firewall — only SSH + HTTPS reach the box.
sudo apt -y install ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 443/tcp
sudo ufw --force enable

# Brute-force protection + automatic security updates
sudo apt -y install fail2ban unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades   # choose "Yes"
```

Harden SSH (`sudo nano /etc/ssh/sshd_config`): set `PasswordAuthentication no`
and `PermitRootLogin no`, then `sudo systemctl restart ssh`.

---

## 2. PostgreSQL

```bash
sudo apt -y install postgresql postgresql-contrib
sudo systemctl enable --now postgresql
```

Postgres listens on localhost by default on Ubuntu — keep it that way (never
expose 5432). Create the bot database, the read-write **owner** role, and the
read-only **dashboard** role:

```bash
sudo -u postgres psql <<'SQL'
CREATE ROLE trader LOGIN PASSWORD 'STRONG_BOT_DB_PASSWORD';
CREATE DATABASE predicted OWNER trader;
SQL
```

Now create the read-only role **as the owner** so default privileges apply to
the bot's future tables. Use the repo's script:

```bash
# copy infra/postgres/01_dashboard_readonly_role.sql to the server first
# (edit it: set a STRONG dashboard_ro password, DB name is `predicted`)
sudo -u postgres psql -d predicted -f 01_dashboard_readonly_role.sql
```

Verify the read-only role really cannot write:

```bash
sudo -u postgres psql -d predicted -c \
 "SELECT has_table_privilege('dashboard_ro','trades','SELECT') AS can_read,
         has_table_privilege('dashboard_ro','trades','INSERT') AS can_write;"
# expect: can_read = t,  can_write = f   (after the schema exists — see step 3)
```

---

## 3. Deploy the bot (`/opt/bot`)

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin bot
sudo apt -y install python3-venv python3-pip rsync git
sudo mkdir -p /opt/bot /etc/bot
```

Get the code onto the box (either `git clone` your repo or `rsync` your local
`apps/bot/` up). Example with rsync from your laptop:

```bash
# from your machine:
rsync -avz --exclude .venv --exclude __pycache__ --exclude '*.log' \
  apps/bot/ deploy@SERVER_IP:/tmp/bot/
# on the server:
sudo cp -r /tmp/bot/. /opt/bot/ && sudo chown -R bot:bot /opt/bot
```

Create the virtualenv and install **pinned** deps (see CODE_REVIEW P3 — pin
versions for reproducibility):

```bash
sudo -u bot python3 -m venv /opt/bot/.venv
sudo -u bot /opt/bot/.venv/bin/pip install -U pip
sudo -u bot /opt/bot/.venv/bin/pip install -r /opt/bot/requirements.txt
```

Write the bot env (`sudo nano /etc/bot/bot.env`) — **paper keys**, `DRY_RUN=false`
so it actually places *paper* orders, trailing stop on:

```ini
# Alpaca PAPER (https://app.alpaca.markets → Paper account → API keys)
ALPACA_API_KEY_ID=PK...
ALPACA_API_SECRET_KEY=...
ALPACA_BASE_URL=https://paper-api.alpaca.markets
ALPACA_DATA_FEED=iex

# Read-WRITE owner role
DATABASE_URL=postgresql+psycopg2://trader:STRONG_BOT_DB_PASSWORD@127.0.0.1:5432/predicted

# Engine: place real PAPER orders (NOT a dry run) for the 2-month test
DRY_RUN=false
RISK_PER_TRADE=0.01
MAX_POSITION_FRACTION=0.33
POLL_SECONDS=60

# Trailing stop (validated defaults — see STRATEGY_REVIEW.md)
USE_TRAILING_STOP=true
TRAIL_BREAKEVEN_ATR=1.5
TRAIL_DISTANCE_ATR=4.0
```

```bash
sudo chown bot:bot /etc/bot/bot.env && sudo chmod 600 /etc/bot/bot.env
```

Create the schema, then install the service:

```bash
# init the tables (installs the uuidv7() fallback if PG < 18)
sudo -u bot bash -c 'cd /opt/bot && EnvironmentFile=/etc/bot/bot.env \
  /opt/bot/.venv/bin/python main.py --init-db'
# ^ if env isn't picked up, prefix the vars or use: set -a; . /etc/bot/bot.env; set +a

# smoke-test ONE cycle (logs to stdout, exits)
sudo -u bot bash -c 'set -a; . /etc/bot/bot.env; set +a; cd /opt/bot && \
  /opt/bot/.venv/bin/python main.py --once'

# install + start the unit (infra/systemd/bot.service)
sudo cp /opt/bot/../infra/systemd/bot.service /etc/systemd/system/ 2>/dev/null \
  || sudo cp bot.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now bot
journalctl -u bot -f          # watch it run
```

> The engine sleeps while the market is closed and evaluates every
> `POLL_SECONDS` when open. The first equity row appears after the first cycle
> during market hours — that's what lights up the dashboard.

---

## 4. Deploy the dashboard (`/opt/dashboard`)

Build the **standalone** Next.js artifact on your laptop (or a CI runner), then
ship it. First ensure `next.config.ts` has `output: "standalone"` (the repo is
already configured for a standalone server).

```bash
# on your machine, in apps/dashboard:
npm ci
npm run build
# bundle the standalone server + static assets:
rsync -avz .next/standalone/ deploy@SERVER_IP:/tmp/dash/
rsync -avz .next/static/      deploy@SERVER_IP:/tmp/dash/.next/static/
rsync -avz public/            deploy@SERVER_IP:/tmp/dash/public/
```

```bash
# on the server:
sudo useradd --system --create-home --shell /usr/sbin/nologin dashboard
sudo mkdir -p /opt/dashboard /etc/dashboard
sudo cp -r /tmp/dash/. /opt/dashboard/ && sudo chown -R dashboard:dashboard /opt/dashboard
```

Generate the dashboard secrets (admin password hash, AUTH_SECRET, encrypted
TOTP). Run the repo's setup script once (it prints a QR + the env values):

```bash
# easiest: run setup:admin on your laptop against a scratch env, copy the output.
# It produces AUTH_SECRET, ADMIN_USERNAME, ADMIN_PASSWORD_HASH,
# TOTP_ENCRYPTION_KEY, TOTP_SECRET_ENC — scan the QR into your authenticator.
cd apps/dashboard && npm run setup:admin
```

Write `/etc/dashboard/dashboard.env`:

```ini
# Read-ONLY role — the dashboard physically cannot write
DATABASE_URL=postgres://dashboard_ro:STRONG_RO_PASSWORD@127.0.0.1:5432/predicted
DB_POOL_MAX=5

# Must equal the public HTTPS URL Caddy serves (see step 5)
AUTH_URL=https://SERVER-IP-DASHES.sslip.io
NODE_ENV=production

AUTH_SECRET=...                 # from setup:admin
ADMIN_USERNAME=...
ADMIN_PASSWORD_HASH=...         # base64 of the bcrypt hash
TOTP_ENCRYPTION_KEY=...
TOTP_SECRET_ENC=...
```

```bash
sudo chown dashboard:dashboard /etc/dashboard/dashboard.env
sudo chmod 600 /etc/dashboard/dashboard.env

# install Node 22 (NodeSource) if not present, then the unit:
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt -y install nodejs
sudo cp infra/systemd/dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now dashboard
journalctl -u dashboard -f
```

---

## 5. TLS reverse proxy (Caddy, automatic HTTPS)

The session cookie is `__Secure-`, so the dashboard **requires HTTPS**. Let's
Encrypt won't issue for a bare IP, so use the free `sslip.io` hostname that maps
to your IP (no signup):

```bash
sudo apt -y install debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt -y install caddy
```

Use `infra/reverse-proxy/Caddyfile` (Option A). Replace the address with your IP
in dashed form — e.g. IP `5.75.123.45` → `5-75-123-45.sslip.io`. **Add login
rate-limiting (CODE_REVIEW P1):**

```caddyfile
5-75-123-45.sslip.io {
    encode zstd gzip
    # Throttle auth endpoints: 10 requests / minute / client IP
    @auth path /login /api/auth/*
    rate_limit @auth {
        zone auth { key {remote_host}  events 10  window 1m }
    }
    reverse_proxy 127.0.0.1:3000
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Frame-Options "DENY"
        X-Content-Type-Options "nosniff"
        Referrer-Policy "strict-origin-when-cross-origin"
        -Server
    }
    log { output file /var/log/caddy/dashboard.access.log }
}
```

> `rate_limit` needs Caddy's `caddy-ratelimit` module. If your `caddy` build
> lacks it, install via `xcaddy build --with github.com/mholt/caddy-ratelimit`
> or apply the limit at fail2ban instead. The rest works on stock Caddy.

```bash
sudo cp infra/reverse-proxy/Caddyfile /etc/caddy/Caddyfile   # after editing
sudo systemctl reload caddy
```

Set `AUTH_URL` in `/etc/dashboard/dashboard.env` to the **same** `https://…sslip.io`,
then `sudo systemctl restart dashboard`. Browse to the URL, log in with your
admin username + password + the 6-digit code from your authenticator. 🎉

---

## 6. Backups, logs, monitoring

```bash
# Nightly pg_dump, keep 14 days (as the postgres user)
sudo -u postgres bash -c 'mkdir -p ~/backups'
echo '0 3 * * * postgres pg_dump -Fc predicted > ~/backups/predicted-$(date +\%F).dump && find ~/backups -mtime +14 -delete' \
  | sudo tee /etc/cron.d/predicted-backup
```

- **Logs:** `journalctl -u bot -f` and `journalctl -u dashboard -f`. journald is
  size-capped by default; cap explicitly with `SystemMaxUse=` in
  `/etc/systemd/journald.conf` if you like.
- **Liveness:** `systemctl status bot dashboard caddy postgresql`. Both units
  `Restart=on-failure`, so a crash self-heals.
- **Disk/mem:** `htop`, `df -h`. CX22 has ample room for this workload.
- (Optional) Point Hetzner's monitoring or a free Uptime check at the HTTPS URL.

---

## 7. Run the 2-month paper trade

**Goal:** confirm the live engine behaves like the backtest before real money.

1. **Confirm it's paper, not dry-run.** `ALPACA_BASE_URL` contains `paper`,
   `DRY_RUN=false`. The bot now submits orders to the Alpaca **paper** account —
   real fills, fake money. Verify in the Alpaca paper dashboard that orders and
   positions appear, and in your dashboard that `trades` / `daily_metrics` rows
   accumulate.
2. **Let it run untouched** for ~8 weeks across varied market conditions. Don't
   tweak params mid-run — that resets your sample.
3. **Weekly, check on the dashboard + Alpaca:**
   - Equity curve trending as expected; max drawdown within the backtest
     envelope (roughly ≤ −8% on this book).
   - **Fills vs the 0.10% slippage assumption** — compare your average
     entry/exit fills to the signal price. If real slippage is consistently
     worse, the faster sleeves (QQQ/EWG/EEM) lose edge first (STRATEGY_REVIEW §4).
   - Trailing stops moving: look for `TRAIL stop X -> Y` lines
     (`journalctl -u bot | grep TRAIL`) and for closed trades whose stop is above
     entry (profit locked).
   - Win rate **25–35% is expected and fine** — judge on Sharpe / profit factor /
     drawdown, not hit rate.
4. **Watch for operational issues:** websocket disconnects (the bot falls back to
   bar-close stop detection and logs a warning), missed cycles, DB errors. The
   engine logs and continues; investigate anything recurring.

### Go-live decision (after ~2 months)

Promote to **live** only if all hold:

- [ ] Realized results broadly track the backtest (return/DD/Sharpe in range).
- [ ] Real slippage ≈ the 0.10% assumption (or better).
- [ ] No recurring operational failures (stops fired correctly, no orphan
      orders, restarts recovered open positions from the DB).
- [ ] You've closed CODE_REVIEW **P1** items (login rate-limit, UTC clock).

To go live: create **live** Alpaca keys, set `ALPACA_BASE_URL=https://api.alpaca.markets`
and the live key/secret in `/etc/bot/bot.env`, **start with the smallest capital
you're willing to lose**, then `sudo systemctl restart bot`. Keep `DRY_RUN=false`.
Consider `USE_PROTECTIVE_STOP=true` only if you switch to whole-share sizing
(fractional shares can't carry a broker stop) — and first verify
`replace_protective_stop` against a real OTO order (CODE_REVIEW P2).

---

## Quick command reference

```bash
sudo systemctl status bot dashboard caddy postgresql   # health
journalctl -u bot -f                                   # bot logs
journalctl -u bot | grep TRAIL                         # trailing-stop moves
sudo systemctl restart bot                             # after editing /etc/bot/bot.env
sudo -u postgres psql -d predicted -c \
  "SELECT count(*) FROM trades; SELECT max(date) FROM daily_metrics;"
```
