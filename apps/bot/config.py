"""Central configuration: loads secrets from .env and defines per-asset strategy specs.

All credentials are read from the environment (populated from a local ``.env`` file
that is git-ignored). Nothing secret is ever hard-coded here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _build_database_url() -> str:
    """Prefer an explicit DATABASE_URL, otherwise assemble one from DB_* parts."""
    explicit = os.getenv("DATABASE_URL")
    if explicit:
        return explicit
    user = os.getenv("DB_USER", "trader")
    password = os.getenv("DB_PASSWORD", "")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "trading_bot")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"


@dataclass(frozen=True)
class Settings:
    alpaca_key_id: str = os.getenv("ALPACA_API_KEY_ID", "")
    alpaca_secret_key: str = os.getenv("ALPACA_API_SECRET_KEY", "")
    alpaca_base_url: str = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    alpaca_data_feed: str = os.getenv("ALPACA_DATA_FEED", "iex")

    database_url: str = _build_database_url()

    risk_per_trade: float = float(os.getenv("RISK_PER_TRADE", "0.01"))
    atr_period: int = int(os.getenv("ATR_PERIOD", "14"))
    poll_seconds: int = int(os.getenv("POLL_SECONDS", "60"))
    dry_run: bool = os.getenv("DRY_RUN", "true").strip().lower() in ("1", "true", "yes")
    # Cap each position's notional to this fraction of account equity. On a small
    # single account the ATR risk target alone wants ~100-200% of equity per
    # position, so this cap controls how concentrated/deployed the account runs.
    # 0.20 -> ~5 diversified positions (defensive); 0.70 -> ~1-2 concentrated
    # positions but ~2x the return (aggressive — single-position tail risk is the
    # cost). Backtest 6y: 0.20=8.9% CAGR/-6.9%DD, 0.70=17.4% CAGR/-11.3%DD.
    max_position_fraction: float = float(os.getenv("MAX_POSITION_FRACTION", "0.33"))
    # Attached broker stop-loss (OTO) requires WHOLE-share orders — Alpaca rejects
    # stops on fractional quantities. A small fractional account therefore runs
    # WITHOUT a broker stop (default) and relies on each strategy's own exit
    # signal; the max_position_fraction cap bounds per-trade exposure instead.
    # Set True only when sizing whole shares (fractional=False).
    use_protective_stop: bool = os.getenv("USE_PROTECTIVE_STOP", "false").strip().lower() in ("1", "true", "yes")

    # Trailing stop. The entry stop is the fixed 1-ATR/1%-equity hard stop; when
    # this is enabled the protective stop then RATCHETS in the trade's favour as
    # price runs, never loosening:
    #   * once price has run ``trail_breakeven_atr`` ATR in profit, the stop is
    #     pulled up to breakeven (entry) — the trade can no longer become a loss;
    #   * beyond that, the stop trails ``trail_distance_atr`` ATR behind the best
    #     price reached, locking in an increasing share of an extended trend.
    # This protects winners on long trends (especially GLD/SLV) that the slow
    # EMA/MACD exit would otherwise give back. On a fractional account it is
    # enforced by the same in-loop software stop as the fixed stop; with
    # use_protective_stop=True the resting broker stop is amended each cycle.
    #
    # Defaults (breakeven 1.5 ATR / trail 4 ATR) were chosen by A/B backtest
    # (backtester.py --account 2000 --years 3, vs --no-trailing). On the 6-asset
    # book this beat BOTH no-trailing and a tighter 1/2-ATR trail on every metric
    # that matters here:
    #              trades  win%   PF   Sharpe  MaxDD   total   folds+
    #   no trail     311  26.4% 2.24   1.48   -12.1%  +62.6%   3/4
    #   trail 1/2   1144  27.7% 1.36   1.48    -4.6%  +43.9%   3/4 (fold1 -0.43)
    #   trail 1.5/4  618  24.6% 1.96   2.16    -4.9%  +82.6%   4/4  <-- default
    # A tight 2-ATR trail whipsaws the faster 1Hour sleeves (QQQ churned to
    # PF 0.89 / 643 trades); 4 ATR lets trends breathe while still ~halving the
    # drawdown. Re-validate with your own data before trusting these live.
    use_trailing_stop: bool = os.getenv("USE_TRAILING_STOP", "true").strip().lower() in ("1", "true", "yes")
    trail_breakeven_atr: float = float(os.getenv("TRAIL_BREAKEVEN_ATR", "1.5"))
    trail_distance_atr: float = float(os.getenv("TRAIL_DISTANCE_ATR", "4.0"))

    # Market-regime filter. When enabled, NEW long entries in the equity-beta
    # sleeve (QQQ/EWG/EEM — the correlation group) are only allowed while the
    # broad gauge (REGIME_SYMBOL, default SPY) is above its REGIME_SMA-day SMA.
    # GLD/SLV/UUP are never gated (UUP is the risk-OFF hedge). The intent is to
    # skip counter-trend equity entries that cluster the small losers, lifting
    # win rate / profit factor at the cost of fewer trades. OFF by default — A/B
    # it first: `python backtester.py --account 2000 --years 3 --regime` vs the
    # plain run (see strategies/regime.py and STRATEGY_REVIEW.md §3.1).
    use_regime_filter: bool = os.getenv("USE_REGIME_FILTER", "false").strip().lower() in ("1", "true", "yes")
    regime_symbol: str = os.getenv("REGIME_SYMBOL", "SPY")
    regime_sma: int = int(os.getenv("REGIME_SMA", "200"))

    # --- Portfolio safety guards (risk/guards.py) ---------------------------
    # Drawdown circuit-breaker: stop opening NEW positions once account equity is
    # down this fraction from its high-water mark; resume once it recovers above
    # ``drawdown_resume_pct``. Open positions keep being managed (the stop still
    # protects them). Defaults are set ABOVE the validated backtest drawdown
    # (~-5% with trailing) so the breaker is pure live tail-insurance and does
    # NOT alter normal operation — set to 0 to disable.
    drawdown_halt_pct: float = float(os.getenv("DRAWDOWN_HALT_PCT", "0.10"))
    drawdown_resume_pct: float = float(os.getenv("DRAWDOWN_RESUME_PCT", "0.06"))
    # Daily max-loss kill-switch: stop opening new positions for the rest of the
    # (UTC) day once the day's REALIZED loss exceeds this fraction of the day's
    # starting equity. Catches a cascade / bad-data day. 0 disables.
    max_daily_loss_pct: float = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.06"))

    # MACD exit gating (STRATEGY_IMPROVEMENTS #4). When True, macd_trend closes a
    # long only when MACD crosses down AND price is below its SMA trend filter,
    # instead of on every MACD<signal cross — so a winner isn't cut on noise
    # while the trend is intact (the trailing stop still protects it).
    #
    # A/B-validated ON (backtester.py --account 2000 --years 3, vs the legacy
    # exit). Full book improved on every metric and got more robust in
    # walk-forward (weakest fold Sharpe 0.77 -> 1.46):
    #                trades  win%   PF   Sharpe  MaxDD   total
    #   legacy exit     615  24.4% 1.97   2.20   -4.9%  +84.0%
    #   gated  exit     584  24.1% 2.03   2.22   -4.6%  +87.0%  <-- default
    # EWG benefits most (PF 1.36 -> 1.72); SLV ~neutral; fewer trades = less
    # slippage drag. Set false to revert. Re-validate on your own data.
    macd_exit_requires_trend: bool = os.getenv("MACD_EXIT_REQUIRES_TREND", "true").strip().lower() in ("1", "true", "yes")

    # Re-entry cooldown (STRATEGY_IMPROVEMENTS #4b). After a position is STOPPED
    # OUT of a symbol, block re-entering that symbol for this many of its bars.
    # The state-based strategies otherwise re-enter immediately into the same
    # chop that just stopped them — diagnosis showed ~47% of trades are <24h
    # whipsaws at ~3% win rate, dragging the book. A cooldown skips those
    # quick-fail re-entries while keeping the long-trend winners (the edge).
    # 0 disables. A/B showed a blanket cooldown HURTS (it also blocks the
    # re-entries that resume into the big winners), so it stays off — kept only
    # as an available lever. Use the ADX filter below instead.
    reentry_cooldown_bars: int = int(os.getenv("REENTRY_COOLDOWN_BARS", "0"))

    # ADX trend-strength entry filter (STRATEGY_IMPROVEMENTS #4c). Only OPEN a new
    # position when ADX(adx_period) >= adx_min, i.e. a real directional trend is
    # present. Diagnosis showed ~47% of trades are <24h whipsaws (~3% win) that
    # cluster in chop; ADX filters by trend QUALITY (unlike the blanket cooldown),
    # preferentially removing the quick-fails while keeping the long runners.
    # 0 disables. A/B-tuned default below.
    adx_min: float = float(os.getenv("ADX_MIN", "0"))
    adx_period: int = int(os.getenv("ADX_PERIOD", "14"))

    # Initial stop width in ATRs (STRATEGY_IMPROVEMENTS #4d). The position is
    # always sized so a stop-out loses exactly RISK_PER_TRADE — so a WIDER stop
    # just means a SMALLER position with more room to ride out entry noise (the
    # diagnosis showed ~47% of trades stop out as <24h whipsaws). Unlike entry
    # filters (cooldown/ADX, which both HURT by reshuffling which trades the
    # shared cash funds), this just gives every trade room and keeps $ risk flat.
    #
    # A/B-tuned (3y, $2k) — 1.5 ATR is the Sharpe-optimal sweet spot and the
    # single biggest win this pass; it lifts BOTH win rate and Sharpe:
    #     mult  trades  win%   PF   Sharpe  MaxDD
    #     1.0     584  24.3% 2.02   2.20   -5.0%   (old)
    #     1.5     493  28.8% 2.24   2.45   -5.6%   <-- default
    #     2.0     469  31.6% 2.08   2.20   -5.2%   (higher win%, lower Sharpe)
    # Wider than ~2 keeps raising win rate but bleeds Sharpe (positions too small,
    # give back too much). Re-validate on your own data.
    stop_atr_mult: float = float(os.getenv("STOP_ATR_MULT", "1.5"))

    # Scale-out / partial profit-taking (STRATEGY_IMPROVEMENTS #6). At
    # +SCALE_OUT_ATR ATR of profit, sell SCALE_OUT_FRAC of the position (a resting
    # limit) and let the rest ride under the trailing stop.
    #
    # A/B-tested and kept OFF by design. Best config (0.33 @ +3 ATR) nearly
    # DOUBLES win rate but LOWERS return and Sharpe — it caps the big runners
    # that are this trend book's whole edge ("higher win rate, less money"):
    #                       win%   PF   Sharpe  MaxDD   total
    #   off (default)      28.8%  2.24   2.45   -5.6%  +102%   <-- keep
    #   0.33 @ +3 ATR      46.0%  1.98   2.36   -4.8%   +81%
    # NOTE: currently evaluated in the BACKTESTER only — the live engine does not
    # yet enforce a partial close, so keep SCALE_OUT_FRAC=0 in live .env until
    # that path is wired. Turn on only if you deliberately prefer a high hit-rate
    # / shallower-drawdown profile over total return.
    scale_out_frac: float = float(os.getenv("SCALE_OUT_FRAC", "0"))
    scale_out_atr: float = float(os.getenv("SCALE_OUT_ATR", "3.0"))

    # --- Alerting (notify.py) ----------------------------------------------
    # Optional Telegram push for stop-outs, guard trips, cycle errors and the
    # daily summary. Leave blank to log-only (no external calls).
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    @property
    def alpaca_paper(self) -> bool:
        """alpaca-py selects the endpoint via a ``paper`` flag, not a base URL.

        We derive it from ALPACA_BASE_URL so the same .env drives either SDK.
        """
        return "paper" in self.alpaca_base_url.lower()

    def validate(self) -> None:
        """Fail fast on missing creds OR an out-of-range setting.

        Catching a bad config at startup (rather than mid-session with real
        money) is the cheapest safety control there is (STRATEGY_IMPROVEMENTS
        #23). Checks required Alpaca creds plus sane ranges for every risk knob.
        """
        missing = []
        if not self.alpaca_key_id:
            missing.append("ALPACA_API_KEY_ID")
        if not self.alpaca_secret_key:
            missing.append("ALPACA_API_SECRET_KEY")
        if missing:
            raise RuntimeError(
                "Missing required environment variables: "
                + ", ".join(missing)
                + ". Copy .env.example to .env and fill it in."
            )

        problems = []
        if not 0 < self.risk_per_trade <= 0.05:
            problems.append(f"RISK_PER_TRADE must be in (0, 0.05]; got {self.risk_per_trade}")
        if self.atr_period < 2:
            problems.append(f"ATR_PERIOD must be >= 2; got {self.atr_period}")
        if self.poll_seconds < 1:
            problems.append(f"POLL_SECONDS must be >= 1; got {self.poll_seconds}")
        if not 0 < self.max_position_fraction <= 1.0:
            problems.append(
                f"MAX_POSITION_FRACTION must be in (0, 1]; got {self.max_position_fraction}"
            )
        if self.use_trailing_stop and self.trail_distance_atr < self.trail_breakeven_atr:
            problems.append(
                f"TRAIL_DISTANCE_ATR ({self.trail_distance_atr}) must be >= "
                f"TRAIL_BREAKEVEN_ATR ({self.trail_breakeven_atr})"
            )
        if not 0 <= self.drawdown_halt_pct < 1:
            problems.append(f"DRAWDOWN_HALT_PCT must be in [0, 1); got {self.drawdown_halt_pct}")
        if self.drawdown_halt_pct and self.drawdown_resume_pct > self.drawdown_halt_pct:
            problems.append(
                f"DRAWDOWN_RESUME_PCT ({self.drawdown_resume_pct}) must be <= "
                f"DRAWDOWN_HALT_PCT ({self.drawdown_halt_pct})"
            )
        if not 0 <= self.max_daily_loss_pct < 1:
            problems.append(f"MAX_DAILY_LOSS_PCT must be in [0, 1); got {self.max_daily_loss_pct}")
        if problems:
            raise RuntimeError("Invalid configuration:\n  - " + "\n  - ".join(problems))


settings = Settings()


# ---------------------------------------------------------------------------
# Per-asset strategy specification.
#
#   strategy   -> registry key in strategies/__init__.py
#   timeframe  -> bar size requested from Alpaca
#   lookback   -> number of bars to pull each cycle (enough to warm up indicators)
#   allow_short-> whether the strategy may take short positions
#   params     -> strategy-specific knobs
# ---------------------------------------------------------------------------
# Focused 6-asset book, redesigned 2026-06 for COST ROBUSTNESS and diversification
# (replaces the prior 7-asset config whose edge leaned on a 15Min SLV sleeve that
# collapsed at realistic slippage). Design rules:
#   * Long-only — a ~$2k Alpaca cash account cannot short (and fractional shorts
#     are disallowed), so every sleeve must be executable without margin.
#   * Slow timeframes only (1Hour / 4Hour / 1Day) — NO 15Min. Lower turnover is
#     what survives 0.10-0.15% slippage (validated: this book holds up, the old
#     one did not).
#   * Diversified across six distinct buckets — US growth / gold / silver /
#     Europe (DM) / emerging markets / US-dollar — with UUP as the risk-OFF hedge
#     that carried the 2022 bear. Equity beta (QQQ/EWG/EEM) is capped to 2
#     concurrent longs by the correlation filter (risk/risk_manager.py).
#   * Removed XLI (only ~19 trades in 5y — statistical noise) and XLK (momentum
#     that lost almost all its edge to slippage).
# OOS stats below are from optimize.py walk-forward folds at 0.05% slippage —
# treat as optimistic (in-sample selection); plan with backtester.py at 0.10%.
ASSET_CONFIGS: dict[str, dict] = {
    # US growth. Proven trend sleeve, the most cost-robust name in the book.
    # OOS median Sharpe 1.66 | 80% folds+ | PF 2.57 | 117 trades
    "QQQ": {
        "strategy": "trend_following",
        "timeframe": "1Hour",
        "lookback": 452,
        "allow_short": False,
        "params": {"fast": 20, "slow": 200},
    },
    # Gold — slow trend, low turnover diversifier.
    # OOS median Sharpe 1.31 | 70% folds+ | PF 3.48 | 82 trades
    "GLD": {
        "strategy": "trend_following",
        "timeframe": "4Hour",
        "lookback": 360,
        "allow_short": False,
        "params": {"fast": 10, "slow": 50},
    },
    # Silver — DE-RISKED from 15Min/426-trades to 4Hour MACD/~70-trades so the
    # edge survives realistic slippage. OOS median Sharpe 1.03 | 70% folds+ | PF 2.36
    "SLV": {
        "strategy": "macd_trend",
        "timeframe": "4Hour",
        "lookback": 360,
        "allow_short": False,
        "params": {"fast_macd": 12, "slow_macd": 26, "signal_macd": 9, "trend_filter": 100},
    },
    # Europe (developed) — MACD trend, shallow drawdown (-6.6%), high consistency.
    # OOS median Sharpe 1.29 | 90% folds+ | PF 1.93 | 150 trades
    "EWG": {
        "strategy": "macd_trend",
        "timeframe": "1Hour",
        "lookback": 320,
        "allow_short": False,
        "params": {"fast_macd": 12, "slow_macd": 26, "signal_macd": 9, "trend_filter": 100},
    },
    # Emerging markets — volume-confirmed momentum, very shallow drawdown (-5.7%).
    # OOS median Sharpe 0.95 | 80% folds+ | PF 1.78 | 115 trades
    "EEM": {
        "strategy": "momentum",
        "timeframe": "1Hour",
        "lookback": 320,
        "allow_short": False,
        "params": {"period": 10, "vol_mult": 2.0, "exit_period": 5},
    },
    # US dollar — the risk-OFF hedge (positive through the 2022 bear). Low turnover.
    # OOS median Sharpe 1.03 | 80% folds+ | PF 3.31 | 48 trades
    "UUP": {
        "strategy": "trend_following",
        "timeframe": "4Hour",
        "lookback": 360,
        "allow_short": False,
        "params": {"fast": 10, "slow": 100},
    },
}