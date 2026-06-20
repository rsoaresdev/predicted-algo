"""Risk management: ATR-based sizing, the hard 1% stop, and the correlation filter.

Sizing rule (the core requirement):
    1 ATR of price movement must equal exactly ``risk_per_trade`` (1%) of equity.

        qty = (equity * risk_per_trade) / ATR

With that sizing, a 1-ATR adverse move loses exactly ``risk_per_trade`` of equity,
so the hard stop is placed exactly 1 ATR from entry - which is also the maximum
allowed loss per trade (1% of equity). The two requirements are one and the same.

Position sizing supports fractional quantities (Alpaca allows fractional market,
limit, stop and stop-limit orders, long-only, min $1 notional per order). Pass
``fractional=False`` to fall back to whole-share sizing.

Pure-Python module (math + logging only) so it is trivially unit-testable.
"""
from __future__ import annotations

import logging
import math
from typing import Mapping, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Correlation groups: assets that tend to move together. Opening too many
# simultaneous long positions within the same group concentrates risk that
# the per-trade 1% ATR sizing does not see (it sizes each trade in
# isolation). Adjust freely as the asset universe changes.
#
#   symbols           -> members of the group (must match ASSET_CONFIGS keys)
#   max_concurrent_long -> how many members may be long at once BEFORE a new
#                          long is blocked (i.e. the (max_concurrent_long+1)-th
#                          simultaneous long gets rejected)
# ---------------------------------------------------------------------------
CORRELATED_GROUPS: dict[str, dict] = {
    # Current universe: QQQ, GLD, SLV, EWG, EEM, UUP. The three equity names
    # (QQQ US-growth, EWG Europe, EEM emerging) all carry risk-on beta that the
    # per-trade 1-ATR sizing can't see, so cap them at 2 concurrent longs — at
    # least one of the (max_position_fraction) slots then stays free for a
    # non-correlated diversifier (GLD / SLV / UUP, all ungrouped). UUP (dollar)
    # is deliberately ungrouped: it is the risk-OFF hedge and should be free to
    # go long exactly when the equities are weak.
    "equity": {
        "symbols": frozenset({"QQQ", "EWG", "EEM"}),
        "max_concurrent_long": 2,
    },
}


class RiskManager:
    def __init__(
        self,
        risk_per_trade: float = 0.01,
        correlation_groups: Optional[dict[str, dict]] = None,
        symbol_risk_overrides: Optional[dict[str, float]] = None,
        max_position_fraction: Optional[float] = None,
        use_trailing_stop: bool = False,
        trail_breakeven_atr: float = 1.0,
        trail_distance_atr: float = 2.0,
        use_regime_filter: bool = False,
        regime_gated_symbols: Optional[frozenset[str]] = None,
        stop_atr_mult: float = 1.0,
    ) -> None:
        # Fraction of equity risked per trade; also ties 1 ATR == this fraction.
        self.risk_per_trade = risk_per_trade
        # Initial protective stop distance in ATRs. The position is sized so that
        # hitting this stop loses exactly ``risk_per_trade`` of equity REGARDLESS
        # of the multiple — a wider stop just means a smaller position, same $
        # risk, but more room to ride out entry noise (fewer whipsaw stop-outs).
        self.stop_atr_mult = stop_atr_mult
        # Trailing-stop knobs (see config.Settings for the rationale). When
        # ``use_trailing_stop`` is False, ``trail_stop`` is a no-op and the stop
        # stays at its fixed 1-ATR entry level — the prior behaviour.
        self.use_trailing_stop = use_trailing_stop
        self.trail_breakeven_atr = trail_breakeven_atr
        self.trail_distance_atr = trail_distance_atr
        # Market-regime gating (see strategies/regime.py). When enabled, new
        # longs in ``regime_gated_symbols`` are blocked while the broad gauge is
        # risk-OFF. Defaults to the equity correlation group so the gated set and
        # the risk-on beta names never drift apart. Ungrouped diversifiers
        # (GLD/SLV/UUP) are never gated.
        self.use_regime_filter = use_regime_filter
        self.regime_gated_symbols = (
            CORRELATED_GROUPS["equity"]["symbols"]
            if regime_gated_symbols is None
            else regime_gated_symbols
        )
        # Per-instance override so experiments (looser/stricter limits, different
        # groupings, or no groups at all) don't require editing this file.
        self.correlation_groups = (
            CORRELATED_GROUPS if correlation_groups is None else correlation_groups
        )
        # Optional per-symbol risk_per_trade override, e.g. to dampen a
        # strategy whose slower signal keeps losing the correlation-group
        # "slot" to faster peers (RiskManager(0.01, symbol_risk_overrides={"SPY": 0.005})).
        self.symbol_risk_overrides = symbol_risk_overrides or {}
        # Cap each position's *notional* to this fraction of account equity.
        # On a small single account the ATR risk target alone wants ~100-200%
        # of equity per position (ATR is a small fraction of price), so without
        # this cap the first signal consumes the whole account and no
        # diversification is possible. e.g. 0.20 -> up to ~5 concurrent positions.
        self.max_position_fraction = max_position_fraction

    def risk_dollars(self, equity: float, symbol: Optional[str] = None) -> float:
        rpt = self.symbol_risk_overrides.get(symbol, self.risk_per_trade) if symbol else self.risk_per_trade
        return equity * rpt

    def position_size(
        self,
        equity: float,
        atr_value: float,
        price: float,
        buying_power: Optional[float] = None,
        fractional: bool = True,
        min_notional: float = 1.0,
        symbol: Optional[str] = None,
    ) -> float:
        """Quantity (fractional if allowed) such that a 1-ATR move == risk_per_trade of equity.

        ``symbol`` is optional and only used to look up a per-symbol
        ``risk_per_trade`` override (see ``symbol_risk_overrides``); omit it
        to use the default ``risk_per_trade`` for every asset, as before.

        Capped by available buying power when provided. Returns 0 on bad inputs
        or when the sized order would fall below Alpaca's $1 minimum notional
        for fractional orders.
        """
        if equity <= 0 or price <= 0 or atr_value is None or atr_value <= 0:
            return 0.0

        # Size against the STOP distance (stop_atr_mult * ATR) so a stop-out always
        # loses exactly risk_per_trade — a wider stop just shrinks the position.
        qty = self.risk_dollars(equity, symbol) / self.stop_distance(atr_value)
        if buying_power is not None and buying_power > 0:
            qty = min(qty, buying_power / price)
        # Cap the position's notional to a fraction of equity so a single trade
        # can't swallow the whole (small) account — the key to diversifying €2k.
        if self.max_position_fraction is not None:
            qty = min(qty, (equity * self.max_position_fraction) / price)

        if not fractional:
            return float(math.floor(qty))

        qty = round(qty, 6)  # Alpaca accepts up to 9 decimals; 6 is comfortably safe
        if qty * price < min_notional:
            return 0.0
        return qty

    def stop_distance(self, atr_value: float) -> float:
        """Hard stop distance in price units == ``stop_atr_mult`` ATRs."""
        return self.stop_atr_mult * atr_value

    def stop_price(self, entry_price: float, side: str, atr_value: float) -> float:
        """Protective stop price, rounded to the penny (Alpaca requires this)."""
        distance = self.stop_distance(atr_value)
        if side == "long":
            return round(entry_price - distance, 2)
        return round(entry_price + distance, 2)

    def max_loss(self, qty: float, atr_value: float) -> float:
        """Dollar loss if the 1-ATR stop is hit. Always <= risk_dollars(equity)."""
        return qty * atr_value

    def trail_stop(
        self,
        side: str,
        entry_price: float,
        atr_value: float,
        extreme_price: float,
        current_stop: float,
    ) -> float:
        """Ratchet a protective stop in the trade's favour as price runs.

        ``extreme_price`` is the best price seen since entry (highest high for a
        long, lowest low for a short). The rule, for a long (mirrored for a short):

          * once ``extreme_price`` reaches ``entry + trail_breakeven_atr*ATR``,
            lock the stop to breakeven (``entry``) — the trade can't go red;
          * always trail ``trail_distance_atr*ATR`` behind ``extreme_price``.

        Because ``extreme_price`` only moves favourably and we never loosen
        ``current_stop``, the returned stop is monotonic (only tightens). With
        the default 1/2 ATR knobs the trail sits below breakeven until price has
        run ~2 ATR, so the stop holds at breakeven between +1 and +2 ATR and only
        then begins to climb — exactly "breakeven first, then trail 2 ATR behind".

        Returns ``current_stop`` unchanged when trailing is disabled or the ATR
        is unusable, so callers can invoke it unconditionally.
        """
        if not self.use_trailing_stop or atr_value is None or atr_value <= 0:
            return current_stop

        breakeven = self.trail_breakeven_atr * atr_value
        distance = self.trail_distance_atr * atr_value

        if side == "long":
            stop = current_stop
            if extreme_price >= entry_price + breakeven:
                stop = max(stop, entry_price)
            stop = max(stop, extreme_price - distance)
            return round(stop, 2)

        # short
        stop = current_stop
        if extreme_price <= entry_price - breakeven:
            stop = min(stop, entry_price)
        stop = min(stop, extreme_price + distance)
        return round(stop, 2)

    def correlation_filter_ok(
        self, symbol: str, position_states: Mapping[str, object]
    ) -> bool:
        """Block a new long in ``symbol`` if too many of its correlated peers
        are already long.

        Looks ``symbol`` up in ``self.correlation_groups``; if it belongs to a
        group, counts how many *other* members of that group are currently
        long and blocks the new entry once ``max_concurrent_long`` is reached.
        Symbols that belong to no group (or no groups at all) are always
        allowed.
        """
        for group_name, group in self.correlation_groups.items():
            symbols = group["symbols"]
            if symbol not in symbols:
                continue

            limit = group["max_concurrent_long"]
            longs = [
                s
                for s in symbols
                if s != symbol
                and getattr(position_states.get(s), "side", None) == "long"
            ]
            if len(longs) >= limit:
                logger.info(
                    "Correlation filter: %s already long (group=%s, limit=%d) - blocking new %s long.",
                    longs,
                    group_name,
                    limit,
                    symbol,
                )
                return False

        return True

    def regime_filter_ok(self, symbol: str, risk_on: bool) -> bool:
        """Block a new long in a gated (equity-beta) symbol while risk is OFF.

        Returns True (allow) when the filter is disabled, when ``symbol`` is not
        a gated name (GLD/SLV/UUP are never gated), or when the broad gauge is
        risk-ON. Returns False only for a gated symbol during a risk-OFF regime.
        ``risk_on`` is supplied by the caller from a :class:`regime.Regime`
        lookup, so this stays pure (no data fetching, trivially testable).
        """
        if not self.use_regime_filter:
            return True
        if symbol not in self.regime_gated_symbols:
            return True
        if not risk_on:
            logger.info(
                "Regime filter: broad gauge risk-OFF — blocking new %s long.", symbol
            )
            return False
        return True