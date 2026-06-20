"""Data-driven strategy optimiser: find the best strategy + parameters per asset.

Fetches N years of Alpaca history and runs every candidate strategy/parameter
combination through the *same* event-driven simulator that backtester.py uses
(so results match production behaviour: 0.05% slippage, 1-ATR hard stop).

Timeframes suportados pelo Alpaca: 15Min, 30Min, 1Hour, 4Hour, 1Day, 1Week.
(2Hour não é suportado — removido.)

Uso rápido:
    python optimize.py --preset default --years 5 --folds 10
    python optimize.py --preset sectors --years 5 --folds 10
    python optimize.py --preset all    --years 5 --folds 10 --resume
    python optimize.py --assets GLD SLV --years 7 --folds 15
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import multiprocessing as mp
import os
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import product as iproduct
from pathlib import Path
from typing import Optional, Type

import pandas as pd

from backtester import (
    DEFAULT_CAPITAL,
    DEFAULT_SLIPPAGE,
    _pf,
    warmup_days,
)
from fast_simulate import SUPPORTED as FAST_SUPPORTED, fast_simulate_asset
from config import settings
from risk import RiskManager
from strategies.base import Strategy

from strategies.mean_reversion import MeanReversionStrategy
from strategies.momentum import MomentumBreakoutStrategy
from strategies.trend_following import TrendFollowingStrategy
from strategies.macd_trend import MACDTrendStrategy
from strategies.volatility_breakout import VolatilityBreakoutStrategy

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ASSET GROUPS — selecciona com --preset <nome>
# ---------------------------------------------------------------------------
ASSET_GROUPS: dict[str, list[str]] = {
    # Grupo original
    "default": ["SPY", "QQQ", "GLD", "TLT", "VGK", "EWJ"],

    # ETFs americanos amplos
    "us_broad": ["SPY", "QQQ", "IWM", "DIA", "MDY"],

    # Sectores americanos (SPDR)
    "sectors": ["XLF", "XLE", "XLK", "XLV", "XLU", "XLI", "XLC", "XLY", "XLB", "XLRE"],

    # Mercados internacionais
    "international": ["VGK", "EWJ", "EEM", "FXI", "EWZ", "EWG", "EWU", "MCHI", "EWA", "EWC"],

    # Obrigações (fixed income)
    "bonds": ["TLT", "IEF", "SHY", "HYG", "LQD", "TIP", "AGG", "BND"],

    # Matérias-primas
    "commodities": ["GLD", "SLV", "USO", "DBA", "PDBC", "CORN", "WEAT"],

    # Alternativos
    "alternatives": ["VNQ", "UUP", "FXE", "BITO"],

    # Tudo (~35 assets) — usa --resume para não perder progresso se interromper
    "all": [
        # US broad
        "SPY", "QQQ", "IWM", "DIA", "MDY",
        # Sectores
        "XLF", "XLE", "XLK", "XLV", "XLU", "XLI", "XLC", "XLY",
        # Internacional
        "VGK", "EWJ", "EEM", "FXI", "EWG", "EWU",
        # Obrigações
        "TLT", "IEF", "HYG", "LQD",
        # Matérias-primas
        "GLD", "SLV", "USO",
        # Alternativos
        "VNQ", "UUP",
    ],
}

DEFAULT_PRESET   = "default"
DEFAULT_YEARS    = 5.0
DEFAULT_MIN_TRADES  = 15
DEFAULT_TOP      = 15
DEFAULT_FOLDS    = 5
DEFAULT_MIN_SHARPE      = 0.4
DEFAULT_MIN_POSITIVE_FRAC = 0.60
DEFAULT_CHECKPOINT = "optimize_results.jsonl"

# ---------------------------------------------------------------------------
# TIMEFRAMES SUPORTADOS PELO ALPACA
# 2Hour foi removido — não é suportado pela API.
# Lookback em barras: quanto o simulator precisa de warmup para a estratégia.
# ---------------------------------------------------------------------------
LOOKBACK_BY_TF: dict[str, int] = {
    "15Min": 600,   # ~1 mês de barras intraday (26 barras/dia)
    "30Min": 450,   # ~3 semanas
    "1Hour": 300,   # ~2 meses de horas
    "4Hour": 350,   # ~14 meses de 4h
    "1Day":  500,   # ~2 anos de dias
    "1Week": 150,   # ~3 anos de semanas
}

STRATEGY_CLASSES: list[Type[Strategy]] = [
    TrendFollowingStrategy,
    MomentumBreakoutStrategy,
    MeanReversionStrategy,
    MACDTrendStrategy,
    VolatilityBreakoutStrategy,
]
NAME_TO_CLASS: dict[str, Type[Strategy]] = {c.name: c for c in STRATEGY_CLASSES}


# ---------------------------------------------------------------------------
# CANDIDATE
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Candidate:
    """Uma combinação de estratégia + timeframe + parâmetros a avaliar."""

    cls: Type[Strategy]
    timeframe: str
    params: dict
    allow_short: bool

    @property
    def family(self) -> str:
        return self.cls.name

    def label(self) -> str:
        p = ", ".join(f"{k}={v}" for k, v in self.params.items())
        short = " /short" if self.allow_short else ""
        return f"{self.family}[{self.timeframe}]({p}){short}"

    def build(self, symbol: str, atr_period: int) -> Strategy:
        lookback = LOOKBACK_BY_TF.get(self.timeframe, 300)
        return self.cls(
            symbol=symbol,
            timeframe=self.timeframe,
            lookback=lookback,
            atr_period=atr_period,
            allow_short=self.allow_short,
            **self.params,
        )


# ---------------------------------------------------------------------------
# SEARCH SPACE
# ---------------------------------------------------------------------------
def build_search_space(fast_only: bool = False, include_weekly: bool = True) -> list[Candidate]:
    """Grid expandido com todos os timeframes suportados pelo Alpaca.

    fast_only=True  → apenas 1Hour + 4Hour  (útil para testes rápidos)
    include_weekly  → inclui 1Week (mais lento mas captura tendências longas)
    """
    cands: list[Candidate] = []

    if fast_only:
        tf_core = ["1Hour", "4Hour"]
    else:
        tf_core = ["15Min", "30Min", "1Hour", "4Hour", "1Day"]
        if include_weekly:
            tf_core.append("1Week")

    # ------------------------------------------------------------------
    # 1. TREND FOLLOWING
    # Melhor edge histórico em GLD, tendências longas — grid amplo.
    # ------------------------------------------------------------------
    fast_emas = [10, 20, 50]
    slow_emas = [50, 100, 200]
    for tf, fast, slow, allow_short in iproduct(tf_core, fast_emas, slow_emas, (False, True)):
        if fast < slow:
            cands.append(Candidate(TrendFollowingStrategy, tf, {"fast": fast, "slow": slow}, allow_short))

    # ------------------------------------------------------------------
    # 2. MOMENTUM BREAKOUT
    # Sem short (breakouts direccionais long-only têm melhor histórico).
    # exit_period = metade do período de entrada, mínimo 5.
    # ------------------------------------------------------------------
    for tf, period, vol_mult in iproduct(tf_core, [10, 20, 40], [1.2, 1.5, 2.0]):
        exit_p = max(5, int(period * 0.5))
        cands.append(Candidate(
            MomentumBreakoutStrategy,
            tf,
            {"period": period, "vol_mult": vol_mult, "exit_period": exit_p},
            False,
        ))

    # ------------------------------------------------------------------
    # 3. MEAN REVERSION
    # Funciona melhor em timeframes curtos (15Min/30Min/1Hour).
    # ------------------------------------------------------------------
    mr_tfs = [t for t in tf_core if t in ("15Min", "30Min", "1Hour", "4Hour")]
    for tf, period, num_std, rsi_os in iproduct(mr_tfs, [14, 20], [2.0, 2.5], [30, 35]):
        cands.append(Candidate(
            MeanReversionStrategy,
            tf,
            {"period": period, "num_std": num_std, "trend_filter": 200, "rsi_oversold": rsi_os},
            False,
        ))

    # ------------------------------------------------------------------
    # 4. MACD TREND
    # Clássico (12/26/9), agressivo (5/35/5) e rápido (3/10/16).
    # ------------------------------------------------------------------
    macd_setups = [(12, 26, 9), (5, 35, 5), (3, 10, 16)]
    for tf, (f, s, sig), trend, allow_short in iproduct(tf_core, macd_setups, [100, 200], (False, True)):
        cands.append(Candidate(
            MACDTrendStrategy,
            tf,
            {"fast_macd": f, "slow_macd": s, "signal_macd": sig, "trend_filter": trend},
            allow_short,
        ))

    # ------------------------------------------------------------------
    # 5. VOLATILITY BREAKOUT (Bollinger)
    # ------------------------------------------------------------------
    for tf, period, std, allow_short in iproduct(tf_core, [20, 40], [2.0, 2.5], (False, True)):
        cands.append(Candidate(
            VolatilityBreakoutStrategy,
            tf,
            {"bb_period": period, "bb_std": std},
            allow_short,
        ))

    logger.info("Search space: %d candidates across %d timeframes", len(cands), len(tf_core))
    return cands


# ---------------------------------------------------------------------------
# WALK-FORWARD FOLDS
# ---------------------------------------------------------------------------
def make_folds(
    test_start: datetime, now: datetime, n_folds: int
) -> list[tuple[datetime, datetime]]:
    n_folds = max(1, n_folds)
    if n_folds == 1:
        return [(test_start, now)]
    span = (now - test_start) / n_folds
    folds = []
    for i in range(n_folds):
        f_start = test_start + span * i
        f_end = now if i == n_folds - 1 else test_start + span * (i + 1)
        folds.append((f_start, f_end))
    return folds


def _slice_until(df: pd.DataFrame, end: datetime) -> pd.DataFrame:
    if isinstance(df.index, pd.DatetimeIndex):
        idx = df.index
        if idx.tz is not None and end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        elif idx.tz is None and end.tzinfo is not None:
            end = end.replace(tzinfo=None)
        return df.loc[:end]
    for col in ("timestamp", "time", "date"):
        if col in df.columns:
            ts = pd.to_datetime(df[col], utc=True)
            cutoff = pd.Timestamp(end)
            if cutoff.tzinfo is None:
                cutoff = cutoff.tz_localize("UTC")
            return df[ts <= cutoff]
    return df


# ---------------------------------------------------------------------------
# DATA FETCH
# ---------------------------------------------------------------------------
def _fetch_all(
    broker,
    space: list[Candidate],
    test_start: datetime,
    now: datetime,
    feed: Optional[str],
    atr_period: int,
    assets: list[str],
) -> dict:
    """Fetch um DataFrame por (asset, timeframe) — warmup máximo por combo."""
    needed: dict[tuple[str, str], int] = {}
    for asset in assets:
        for c in space:
            key = (asset, c.timeframe)
            needed[key] = max(needed.get(key, 0), warmup_days(c.build(asset, atr_period)))

    cache: dict[tuple[str, str], Optional[pd.DataFrame]] = {}
    for (asset, tf), warmup in sorted(needed.items()):
        fetch_start = test_start - timedelta(days=warmup)
        logger.info("Fetching %s (%s) from %s ...", asset, tf, fetch_start.date())
        try:
            df = broker.get_bars_range(asset, tf, fetch_start, now, feed)
            cache[(asset, tf)] = df
        except Exception as exc:
            logger.warning("[%s %s] fetch failed: %s", asset, tf, exc)
            cache[(asset, tf)] = None
    return cache


# ---------------------------------------------------------------------------
# MULTIPROCESSING WORKER
# ---------------------------------------------------------------------------
_WORKER_CACHE: dict = {}
_WORKER_ATR_PERIOD: int = 14
_WORKER_TEST_START = None
_WORKER_MIN_TRADES: int = DEFAULT_MIN_TRADES
_WORKER_FOLDS: list[tuple[datetime, datetime]] = []
_WORKER_MIN_SHARPE: float = DEFAULT_MIN_SHARPE
_WORKER_MIN_POS_FRAC: float = DEFAULT_MIN_POSITIVE_FRAC


def _init_worker(
    cache, atr_period, test_start, min_trades, folds, min_sharpe, min_pos_frac
) -> None:
    global _WORKER_CACHE, _WORKER_ATR_PERIOD, _WORKER_TEST_START, _WORKER_MIN_TRADES
    global _WORKER_FOLDS, _WORKER_MIN_SHARPE, _WORKER_MIN_POS_FRAC
    _WORKER_CACHE = cache
    _WORKER_ATR_PERIOD = atr_period
    _WORKER_TEST_START = test_start
    _WORKER_MIN_TRADES = min_trades
    _WORKER_FOLDS = folds
    _WORKER_MIN_SHARPE = min_sharpe
    _WORKER_MIN_POS_FRAC = min_pos_frac


def _worker(task: tuple) -> Optional[dict]:
    asset, candidate = task
    df = _WORKER_CACHE.get((asset, candidate.timeframe))
    if df is None or df.empty:
        return None

    risk = RiskManager(risk_per_trade=settings.risk_per_trade)
    strat = candidate.build(asset, _WORKER_ATR_PERIOD)

    if len(df) < strat.min_bars():
        return None

    if strat.name not in FAST_SUPPORTED:
        raise ValueError(f"optimize: no fast-path simulator for family '{strat.name}'")

    try:
        full_res = fast_simulate_asset(
            asset, strat, df, _WORKER_TEST_START, DEFAULT_CAPITAL, DEFAULT_SLIPPAGE, risk
        )
    except Exception:
        return None
    fm = full_res.metrics

    fold_sharpes: list[float] = []
    fold_trades: list[int] = []
    for f_start, f_end in _WORKER_FOLDS:
        df_fold = _slice_until(df, f_end)
        if len(df_fold) < strat.min_bars():
            continue
        try:
            r = fast_simulate_asset(
                asset, strat, df_fold, f_start, DEFAULT_CAPITAL, DEFAULT_SLIPPAGE, risk
            )
        except Exception:
            continue
        fold_sharpes.append(r.metrics["sharpe"])
        fold_trades.append(r.metrics["num_trades"])

    n_valid = len(fold_sharpes)
    n_folds = max(1, len(_WORKER_FOLDS))
    oos_median_sharpe = statistics.median(fold_sharpes) if fold_sharpes else None
    oos_frac_positive = (
        sum(1 for s in fold_sharpes if s > 0) / n_valid if n_valid else 0.0
    )

    enough_folds_valid = n_valid >= max(1, math.ceil(n_folds * 0.60))
    eligible = (
        fm["num_trades"] >= _WORKER_MIN_TRADES
        and enough_folds_valid
        and oos_median_sharpe is not None
        and oos_median_sharpe >= _WORKER_MIN_SHARPE
        and oos_frac_positive >= _WORKER_MIN_POS_FRAC
    )

    return {
        "asset": asset,
        "candidate": candidate,
        "trades": fm["num_trades"],
        "win_rate": fm["win_rate"],
        "profit_factor": fm["profit_factor"],
        "max_drawdown": fm["max_drawdown"],
        "sharpe": fm["sharpe"],
        "total_return": fm["total_return"],
        "oos_median_sharpe": oos_median_sharpe,
        "oos_frac_positive": oos_frac_positive,
        "oos_folds_valid": n_valid,
        "oos_folds_total": n_folds,
        "eligible": eligible,
    }


# ---------------------------------------------------------------------------
# CHECKPOINTING
# ---------------------------------------------------------------------------
def _row_to_json(asset: str, row: dict) -> str:
    c: Candidate = row["candidate"]
    payload = {
        "asset": asset,
        "family": c.family,
        "timeframe": c.timeframe,
        "params": c.params,
        "allow_short": c.allow_short,
        "trades": row["trades"],
        "win_rate": row["win_rate"],
        "profit_factor": row["profit_factor"],
        "max_drawdown": row["max_drawdown"],
        "sharpe": row["sharpe"],
        "total_return": row["total_return"],
        "oos_median_sharpe": row["oos_median_sharpe"],
        "oos_frac_positive": row["oos_frac_positive"],
        "oos_folds_valid": row["oos_folds_valid"],
        "oos_folds_total": row["oos_folds_total"],
        "eligible": row["eligible"],
    }
    return json.dumps(payload)


def _json_to_row(payload: dict) -> tuple[str, dict]:
    cls = NAME_TO_CLASS.get(payload["family"])
    if cls is None:
        raise ValueError(f"Unknown strategy family in checkpoint: {payload['family']}")
    candidate = Candidate(
        cls=cls,
        timeframe=payload["timeframe"],
        params=payload["params"],
        allow_short=payload["allow_short"],
    )
    row = {
        "candidate": candidate,
        "trades": payload["trades"],
        "win_rate": payload["win_rate"],
        "profit_factor": payload["profit_factor"],
        "max_drawdown": payload["max_drawdown"],
        "sharpe": payload["sharpe"],
        "total_return": payload["total_return"],
        "oos_median_sharpe": payload["oos_median_sharpe"],
        "oos_frac_positive": payload["oos_frac_positive"],
        "oos_folds_valid": payload["oos_folds_valid"],
        "oos_folds_total": payload["oos_folds_total"],
        "eligible": payload["eligible"],
    }
    return payload["asset"], row


def load_checkpoint(path: Path, assets: list[str]) -> dict[str, list[dict]]:
    results: dict[str, list[dict]] = {a: [] for a in assets}
    if not path.exists():
        return results
    n = 0
    with path.open("r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                asset, row = _json_to_row(payload)
                if asset in results:
                    results[asset].append(row)
                    n += 1
            except Exception as exc:
                logger.warning("Skipping malformed checkpoint line: %s", exc)
    logger.info("Loaded %d completed results from checkpoint %s", n, path)
    return results


# ---------------------------------------------------------------------------
# EVALUATION PIPELINE
# ---------------------------------------------------------------------------
def evaluate_all(
    years: float,
    min_trades: int,
    feed: Optional[str],
    assets: list[str],
    fast_only: bool = False,
    include_weekly: bool = True,
    workers: Optional[int] = None,
    folds: int = DEFAULT_FOLDS,
    min_sharpe: float = DEFAULT_MIN_SHARPE,
    min_positive_frac: float = DEFAULT_MIN_POSITIVE_FRAC,
    checkpoint_path: Optional[Path] = None,
    resume: bool = False,
) -> dict[str, list[dict]]:
    from data import AlpacaBroker

    broker = AlpacaBroker()
    atr_period = settings.atr_period
    space = build_search_space(fast_only=fast_only, include_weekly=include_weekly)

    now = datetime.now(timezone.utc)
    test_start = now - timedelta(days=int(365 * years))
    fold_windows = make_folds(test_start, now, folds)
    logger.info(
        "Walk-forward folds (%d): %s",
        len(fold_windows),
        ", ".join(f"[{s.date()} -> {e.date()}]" for s, e in fold_windows),
    )

    cache = _fetch_all(broker, space, test_start, now, feed, atr_period, assets)

    results: dict[str, list[dict]] = {a: [] for a in assets}
    done_keys: set[tuple[str, str]] = set()

    if resume and checkpoint_path is not None:
        prior = load_checkpoint(checkpoint_path, assets)
        for asset, rows in prior.items():
            for row in rows:
                results[asset].append(row)
                done_keys.add((asset, row["candidate"].label()))
        if done_keys:
            logger.info(
                "Resuming: %d combinations already completed, skipping them.", len(done_keys)
            )

    tasks = [
        (asset, c)
        for asset in assets
        for c in space
        if (asset, c.label()) not in done_keys
    ]

    n_workers = workers or max(1, (os.cpu_count() or 4) - 1)
    total = len(tasks)
    logger.info(
        "Evaluating %d remaining combinations across %d workers (folds=%d) ...",
        total,
        n_workers,
        len(fold_windows),
    )

    if total == 0:
        return results

    chunksize = max(4, total // (n_workers * 20))
    checkpoint_file = checkpoint_path.open("a") if checkpoint_path is not None else None
    done = 0

    try:
        with mp.Pool(
            processes=n_workers,
            initializer=_init_worker,
            initargs=(
                cache, atr_period, test_start, min_trades,
                fold_windows, min_sharpe, min_positive_frac,
            ),
        ) as pool:
            for row in pool.imap_unordered(_worker, tasks, chunksize=chunksize):
                done += 1
                if done % 200 == 0:
                    logger.info(
                        "Progress: %d / %d  (%.0f%%)", done, total, 100 * done / total
                    )
                if row is None:
                    continue
                asset = row.pop("asset")
                results[asset].append(row)
                if checkpoint_file is not None:
                    checkpoint_file.write(_row_to_json(asset, row) + "\n")
                    checkpoint_file.flush()
    finally:
        if checkpoint_file is not None:
            checkpoint_file.close()

    logger.info("Evaluation complete: %d combinations processed.", total)
    return results


# ---------------------------------------------------------------------------
# PICK BEST / HELPERS
# ---------------------------------------------------------------------------
def pick_best(rows: list[dict], min_trades: int) -> tuple[Optional[dict], bool]:
    eligible = [r for r in rows if r["eligible"]]
    if eligible:
        return max(eligible, key=lambda r: r["oos_median_sharpe"]), True
    if not rows:
        return None, False
    return (
        max(
            rows,
            key=lambda r: (
                r["oos_median_sharpe"] if r["oos_median_sharpe"] is not None else -999.0,
                r["trades"],
            ),
        ),
        False,
    )


def family_density(rows: list[dict], candidate: "Candidate") -> tuple[int, int]:
    bucket = [
        r
        for r in rows
        if r["candidate"].family == candidate.family
        and r["candidate"].timeframe == candidate.timeframe
    ]
    elig = sum(1 for r in bucket if r["eligible"])
    return elig, len(bucket)


# ---------------------------------------------------------------------------
# REPORT
# ---------------------------------------------------------------------------
def _config_block(asset: str, row: dict, atr_period: int) -> str:
    cand: Candidate = row["candidate"]
    strat = cand.build(asset, atr_period)
    lookback = max(LOOKBACK_BY_TF.get(cand.timeframe, 300), strat.min_bars() + 250)
    oos = row["oos_median_sharpe"]
    oos_str = f"{oos:.2f}" if oos is not None else "n/a"
    return "\n".join(
        [
            f"    # OOS median Sharpe {oos_str}, {row['oos_frac_positive']*100:.0f}% folds positive"
            f" ({row['oos_folds_valid']}/{row['oos_folds_total']} folds usable)",
            f'    "{asset}": {{',
            f'        "strategy": "{cand.family}",',
            f'        "timeframe": "{cand.timeframe}",',
            f'        "lookback": {lookback},',
            f'        "allow_short": {cand.allow_short},',
            f'        "params": {cand.params!r},',
            "    },",
        ]
    )


def format_report(
    results: dict[str, list[dict]],
    years: float,
    min_trades: int,
    top: int,
    folds: int,
    min_sharpe: float,
    min_positive_frac: float,
    assets: list[str],
) -> str:
    atr_period = settings.atr_period
    sep = "=" * 110
    lines: list[str] = [
        sep,
        f"  STRATEGY OPTIMISATION   ({years:g}y history, {folds} walk-forward fold(s), "
        f"min-trades={min_trades}, min-OOS-sharpe={min_sharpe}, min-positive-frac={min_positive_frac})",
        f"  Assets: {', '.join(assets)}",
        sep,
    ]

    recommended: dict[str, dict] = {}
    for asset in assets:
        rows = sorted(
            results.get(asset, []),
            key=lambda r: (
                r["eligible"],
                r["oos_median_sharpe"] if r["oos_median_sharpe"] is not None else -999.0,
            ),
            reverse=True,
        )
        lines.append(f"  {asset}  ({len(rows)} candidates evaluated)")
        lines.append(
            f"  {'RANK':<5}{'STRATEGY':<48}{'TR':>4}{'WIN%':>7}{'PF':>7}"
            f"{'MAXDD':>8}{'SHARPE':>8}{'OOS-SHP':>9}{'STAB%':>7}{'RET%':>8}"
        )
        lines.append("  " + "-" * 104)
        if not rows:
            lines.append("  (no usable data — fetch failed or insufficient bars)")
            lines.append(sep)
            continue
        for rank, r in enumerate(rows[:top], start=1):
            c: Candidate = r["candidate"]
            mark = "*" if r["eligible"] else " "
            label = c.label()
            if len(label) > 47:
                label = label[:44] + "..."
            oos_disp = (
                f"{r['oos_median_sharpe']:.2f}" if r["oos_median_sharpe"] is not None else "n/a"
            )
            lines.append(
                f"  {str(rank) + mark:<5}{label:<48}{r['trades']:>4}"
                f"{r['win_rate'] * 100:>6.1f}%{_pf(r['profit_factor']):>7}"
                f"{r['max_drawdown'] * 100:>7.1f}%{r['sharpe']:>8.2f}"
                f"{oos_disp:>9}{r['oos_frac_positive'] * 100:>6.0f}%"
                f"{r['total_return'] * 100:>7.1f}%"
            )
        best, confident = pick_best(rows, min_trades)
        if best is not None:
            recommended[asset] = best
            bc: Candidate = best["candidate"]
            elig_count, bucket_size = family_density(rows, bc)
            density_note = (
                f"family robustness: {elig_count}/{bucket_size} similar variants also eligible"
            )
            overfit_note = ""
            if (
                best["oos_median_sharpe"] is not None
                and (best["sharpe"] - best["oos_median_sharpe"]) > 0.5
            ):
                overfit_note = (
                    "  [WARNING: full-window Sharpe much higher than OOS median — possible overfit]"
                )
            flag = (
                ""
                if confident
                else "  [LOW CONFIDENCE — no candidate met the trade+OOS-Sharpe+stability bar]"
            )
            oos_disp = (
                f"{best['oos_median_sharpe']:.2f}"
                if best["oos_median_sharpe"] is not None
                else "n/a"
            )
            lines.append(
                f"  -> RECOMMENDED: {bc.label()}  "
                f"(full Sharpe {best['sharpe']:.2f}, OOS median {oos_disp},"
                f" {best['trades']} trades){flag}"
            )
            lines.append(f"     [{density_note}]{overfit_note}")
        lines.append(
            "  * = eligible (trades + min-trades, OOS median Sharpe + min-sharpe,"
            " fold stability + min-positive-frac)"
        )
        lines.append(sep)

    lines.append("  RECOMMENDED config.py  ASSET_CONFIGS  (paste over the existing dict body):")
    lines.append(sep)
    lines.append("ASSET_CONFIGS: dict[str, dict] = {")
    for asset in assets:
        if asset in recommended:
            lines.append(_config_block(asset, recommended[asset], atr_period))
    lines.append("}")
    lines.append(sep)
    lines.append("  Then run:  python backtester.py   to confirm the non-negative-Sharpe gate.")
    lines.append(sep)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main() -> None:
    # Nomes de presets disponíveis para mostrar no help
    preset_names = ", ".join(ASSET_GROUPS.keys())

    parser = argparse.ArgumentParser(
        description="Find the best strategy+params per asset.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Exemplos:
  python optimize.py --preset default --years 5 --folds 10
  python optimize.py --preset sectors --years 5 --folds 10
  python optimize.py --preset all     --years 5 --folds 10 --resume
  python optimize.py --assets GLD SLV --years 7 --folds 15

Presets disponíveis: {preset_names}
        """,
    )

    # --- asset selection (mutually exclusive: --preset XOR --assets) ---
    asset_group = parser.add_mutually_exclusive_group()
    asset_group.add_argument(
        "--preset",
        metavar="PRESET",
        default=None,
        help=f"Asset group preset. Disponíveis: {preset_names}. (default: '{DEFAULT_PRESET}')",
    )
    asset_group.add_argument(
        "--assets",
        nargs="+",
        metavar="ASSET",
        default=None,
        help="Assets individuais, e.g. --assets GLD SLV QQQ",
    )

    # --- core params ---
    parser.add_argument("--years",      type=float, default=DEFAULT_YEARS,
                        help=f"Anos de histórico (default {DEFAULT_YEARS})")
    parser.add_argument("--min-trades", type=int,   default=DEFAULT_MIN_TRADES,
                        help=f"Mínimo de trades para ser elegível (default {DEFAULT_MIN_TRADES})")
    parser.add_argument("--top",        type=int,   default=DEFAULT_TOP,
                        help=f"Linhas mostradas por asset (default {DEFAULT_TOP})")
    parser.add_argument("--folds",      type=int,   default=DEFAULT_FOLDS,
                        help=f"Folds walk-forward OOS (default {DEFAULT_FOLDS})")
    parser.add_argument("--min-sharpe", type=float, default=DEFAULT_MIN_SHARPE,
                        help=f"Sharpe OOS mediano mínimo (default {DEFAULT_MIN_SHARPE})")
    parser.add_argument("--min-positive-frac", type=float, default=DEFAULT_MIN_POSITIVE_FRAC,
                        help=f"Fracção mínima de folds com Sharpe positivo (default {DEFAULT_MIN_POSITIVE_FRAC})")

    # --- timeframe options ---
    parser.add_argument("--fast-only",     action="store_true",
                        help="Apenas 1Hour + 4Hour (teste rápido)")
    parser.add_argument("--no-weekly",     action="store_true",
                        help="Excluir timeframe 1Week do search space")

    # --- infra ---
    parser.add_argument("--feed",       default=None,
                        help="Override data feed (iex/sip)")
    parser.add_argument("--workers",    type=int, default=None,
                        help="Workers paralelos (default: cpu_count()-1)")
    parser.add_argument("--out",        default=DEFAULT_CHECKPOINT,
                        help=f"Ficheiro checkpoint JSON Lines (default: {DEFAULT_CHECKPOINT})")
    parser.add_argument("--resume",     action="store_true",
                        help="Retomar a partir do --out, saltando combinações já completadas")
    parser.add_argument("--list-presets", action="store_true",
                        help="Listar presets disponíveis e os seus assets, depois sair")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Logging em DEBUG")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # --list-presets
    if args.list_presets:
        print("\nPresets disponíveis:\n")
        for name, symbols in ASSET_GROUPS.items():
            print(f"  {name:<16} ({len(symbols)} assets): {', '.join(symbols)}")
        print()
        return

    # Resolver asset list
    if args.assets:
        _assets = [a.upper() for a in args.assets]
    elif args.preset:
        preset = args.preset.lower()
        if preset not in ASSET_GROUPS:
            parser.error(
                f"Preset '{preset}' desconhecido. Disponíveis: {preset_names}\n"
                f"Usa --list-presets para ver os assets de cada grupo."
            )
        _assets = ASSET_GROUPS[preset]
    else:
        _assets = ASSET_GROUPS[DEFAULT_PRESET]

    logger.info("Assets a avaliar (%d): %s", len(_assets), ", ".join(_assets))

    if hasattr(os, "fork"):
        try:
            mp.set_start_method("fork", force=True)
        except RuntimeError:
            pass

    checkpoint_path = Path(args.out)
    if args.resume and not checkpoint_path.exists():
        logger.warning(
            "--resume pedido mas o checkpoint %s não existe; a começar do zero.",
            checkpoint_path,
        )

    results = evaluate_all(
        years=args.years,
        min_trades=args.min_trades,
        feed=args.feed,
        assets=_assets,
        fast_only=args.fast_only,
        include_weekly=not args.no_weekly,
        workers=args.workers,
        folds=args.folds,
        min_sharpe=args.min_sharpe,
        min_positive_frac=args.min_positive_frac,
        checkpoint_path=checkpoint_path,
        resume=args.resume,
    )

    print(
        format_report(
            results,
            args.years,
            args.min_trades,
            args.top,
            args.folds,
            args.min_sharpe,
            args.min_positive_frac,
            assets=_assets,
        )
    )
    print(
        f"\n  (raw results checkpointed to {checkpoint_path.resolve()}"
        f" -- usa --resume para continuar esta run mais tarde)"
    )


if __name__ == "__main__":
    main()