import json
import sys
from decimal import Decimal, ROUND_DOWN
from typing import Any


class DeterministicBtcBot:
    def __init__(self) -> None:
        self.symbol = "CBBTC"
        self.price_scale = 5
        self.qty_scale = 8
        self.quote_scale = 6

        self.initial_capital = Decimal("1000000000")
        self.cash_quote = Decimal("1000000000")
        self.equity_quote = Decimal("1000000000")
        self.peak_equity_quote = Decimal("1000000000")

        self.pos_qty = Decimal("0")
        self.pos_cost_quote = Decimal("0")
        self.entry_price = Decimal("0")
        self.highest_price_since_entry = Decimal("0")

        self.fee_bps = Decimal("4")
        self.slippage_bps = Decimal("2")
        self.min_notional_quote = Decimal("1000000")
        self.max_weight_bps = Decimal("10000")

        self.closes: list[Decimal] = []
        self.ranges: list[Decimal] = []
        self.ema_fast: Decimal | None = None
        self.ema_slow: Decimal | None = None

        self.pending_order_id: str | None = None
        self.pending_side: str | None = None
        self.pending_since_seq: int = -1

        self.last_exit_seq: int = -100
        self.last_entry_seq: int = -100
        self.completed_trades: int = 0
        self.order_counter: int = 0
        self.trading_halted: bool = False

        # Vyladěné parametry v3
        self.fast_period = 9
        self.slow_period = 36
        self.atr_period = 14
        self.cooldown_bars = 7
        self.min_hold_bars = 4
        self.max_alloc_ratio = Decimal("0.65")
        self.stop_loss_pct = Decimal("0.0070")       # 0.70 % stop-loss
        self.breakeven_trigger = Decimal("0.0030")  # od +0.30 % chráníme zisk
        self.trailing_stop_pct = Decimal("0.0038")   # 0.38 % trailing stop od maxima
        self.take_profit_pct = Decimal("0.0190")     # 1.90 % cílový zisk
        self.hard_dd_limit = Decimal("0.15")

    def _dec(self, val: Any, default: str = "0") -> Decimal:
        if val is None:
            return Decimal(default)
        return Decimal(str(val))

    def _next_client_id(self, prefix: str, seq: int) -> str:
        self.order_counter += 1
        return f"{prefix}-{seq}-{self.order_counter}"

    def on_hello(self, msg: dict[str, Any]) -> None:
        capital = msg.get("capital", {})
        self.quote_scale = int(capital.get("quote_scale", 6))
        self.initial_capital = self._dec(capital.get("quote_units"), "1000000000")
        self.cash_quote = self.initial_capital
        self.equity_quote = self.initial_capital
        self.peak_equity_quote = self.initial_capital

        symbols = msg.get("symbols", [])
        if symbols:
            sym_info = symbols[0]
            self.symbol = str(sym_info.get("symbol", "CBBTC"))
            self.price_scale = int(sym_info.get("price_scale", 5))
            self.qty_scale = int(sym_info.get("qty_scale", 8))

        fees = msg.get("fees", {})
        self.fee_bps = self._dec(fees.get("fee_bps"), "4")
        self.slippage_bps = self._dec(fees.get("slippage_bps"), "2")
        self.min_notional_quote = self._dec(fees.get("min_notional_quote_units"), "1000000")

        caps = msg.get("caps", {})
        self.max_weight_bps = self._dec(caps.get("max_weight_bps"), "10000")

    def _sync_account_and_fills(self, msg: dict[str, Any], seq: int) -> None:
        fills = msg.get("fills") or []
        for f in fills:
            cid = str(f.get("client_id", ""))
            status = str(f.get("status", ""))
            side = str(f.get("side", ""))
            if cid == self.pending_order_id:
                self.pending_order_id = None
                self.pending_side = None
                self.pending_since_seq = -1

            if status == "filled":
                fill_price = self._dec(f.get("price"))
                fill_qty = self._dec(f.get("qty"))
                if side == "buy" and fill_qty > 0:
                    self.entry_price = fill_price
                    self.highest_price_since_entry = fill_price
                    self.last_entry_seq = seq
                elif side == "sell":
                    self.entry_price = Decimal("0")
                    self.highest_price_since_entry = Decimal("0")
                    self.last_exit_seq = seq
                    self.completed_trades += 1

        account = msg.get("account") or {}
        if "cash_quote_units" in account:
            self.cash_quote = self._dec(account["cash_quote_units"])
        if "equity_quote_units" in account:
            self.equity_quote = self._dec(account["equity_quote_units"])
            if self.equity_quote > self.peak_equity_quote:
                self.peak_equity_quote = self.equity_quote

        positions = account.get("positions") or []
        found_pos = False
        for p in positions:
            if str(p.get("symbol")) == self.symbol:
                self.pos_qty = self._dec(p.get("qty"))
                self.pos_cost_quote = self._dec(p.get("cost_quote_units"))
                found_pos = True
                break
        if not found_pos:
            self.pos_qty = Decimal("0")
            self.pos_cost_quote = Decimal("0")

        if self.pending_order_id is not None and (seq - self.pending_since_seq) >= 3:
            self.pending_order_id = None
            self.pending_side = None
            self.pending_since_seq = -1

    def _update_indicators(self, bar: dict[str, Any]) -> tuple[Decimal, Decimal, Decimal]:
        o = self._dec(bar.get("o"))
        h = self._dec(bar.get("h"))
        l = self._dec(bar.get("l"))
        c = self._dec(bar.get("c"))

        prev_c = self.closes[-1] if self.closes else o
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))

        self.closes.append(c)
        self.ranges.append(tr)

        alpha_fast = Decimal("2") / Decimal(str(self.fast_period + 1))
        alpha_slow = Decimal("2") / Decimal(str(self.slow_period + 1))

        if self.ema_fast is None:
            self.ema_fast = c
            self.ema_slow = c
        else:
            self.ema_fast = (c * alpha_fast) + (self.ema_fast * (Decimal("1") - alpha_fast))
            self.ema_slow = (c * alpha_slow) + (self.ema_slow * (Decimal("1") - alpha_slow))

        return c, h, l

    def _get_atr(self) -> Decimal:
        if not self.ranges:
            return Decimal("0")
        window = self.ranges[-self.atr_period :]
        return sum(window, Decimal("0")) / Decimal(str(len(window)))

    def _get_pool_depth_quote(self, bar: dict[str, Any]) -> Decimal:
        c_depth = bar.get("c_depth") or bar.get("o_depth") or []
        if not c_depth:
            return Decimal("0")
        try:
            return self._dec(c_depth[0])
        except Exception:
            return Decimal("0")

    def _compute_buy_notional(self, depth_quote: Decimal, fallback_mode: bool = False) -> Decimal:
        if fallback_mode:
            target = max(self.min_notional_quote * Decimal("2"), Decimal("4000000"))
            target = min(target, self.cash_quote * Decimal("0.15"))
            return target.quantize(Decimal("1"), rounding=ROUND_DOWN)

        max_allowed_by_cap = (self.equity_quote * self.max_weight_bps) / Decimal("10000")
        budget = min(self.cash_quote * self.max_alloc_ratio, max_allowed_by_cap * Decimal("0.85"))

        if depth_quote > 0:
            max_pool_friendly = depth_quote * Decimal("0.0032")
            if max_pool_friendly >= self.min_notional_quote:
                budget = min(budget, max_pool_friendly)

        budget = min(budget, self.cash_quote - Decimal("100000"))
        return budget.quantize(Decimal("1"), rounding=ROUND_DOWN)

    def on_batch(self, msg: dict[str, Any]) -> dict[str, Any]:
        seq = int(msg.get("seq", 0))
        self._sync_account_and_fills(msg, seq)

        bars = msg.get("bars") or []
        target_bar = None
        for b in bars:
            if str(b.get("symbol")) == self.symbol:
                target_bar = b
                break
        if target_bar is None and bars:
            target_bar = bars[0]

        if target_bar is None:
            return {"type": "reply", "seq": seq, "orders": []}

        close_price, high_price, _ = self._update_indicators(target_bar)
        atr = self._get_atr()
        depth_quote = self._get_pool_depth_quote(target_bar)

        orders: list[dict[str, Any]] = []

        if self.peak_equity_quote > 0:
            current_dd = (self.peak_equity_quote - self.equity_quote) / self.peak_equity_quote
            if current_dd >= self.hard_dd_limit:
                self.trading_halted = True

        if self.pending_order_id is not None:
            return {"type": "reply", "seq": seq, "orders": []}

        # 1) Správa otevřené pozice
        if self.pos_qty > 0:
            if high_price > self.highest_price_since_entry:
                self.highest_price_since_entry = high_price

            bars_held = seq - self.last_entry_seq
            should_sell = False

            if self.trading_halted:
                should_sell = True
            elif self.entry_price > 0:
                pnl_pct = (close_price - self.entry_price) / self.entry_price
                max_gain_pct = (self.highest_price_since_entry - self.entry_price) / self.entry_price
                drop_from_peak = (
                    (self.highest_price_since_entry - close_price) / self.highest_price_since_entry
                    if self.highest_price_since_entry > 0
                    else Decimal("0")
                )

                if pnl_pct <= -self.stop_loss_pct:
                    should_sell = True
                elif pnl_pct >= self.take_profit_pct:
                    should_sell = True
                elif max_gain_pct >= self.breakeven_trigger and pnl_pct <= Decimal("0.0008"):
                    should_sell = True
                elif pnl_pct > Decimal("0.0012") and drop_from_peak >= self.trailing_stop_pct:
                    should_sell = True
                elif (
                    bars_held >= self.min_hold_bars
                    and self.ema_fast is not None
                    and self.ema_slow is not None
                    and self.ema_fast < self.ema_slow
                    and pnl_pct > Decimal("0.0010")
                ):
                    should_sell = True
                elif seq >= 1434:
                    should_sell = True

            if should_sell:
                qty_str = str(self.pos_qty.quantize(Decimal("1"), rounding=ROUND_DOWN))
                if Decimal(qty_str) > 0:
                    cid = self._next_client_id("sell", seq)
                    self.pending_order_id = cid
                    self.pending_side = "sell"
                    self.pending_since_seq = seq
                    orders.append(
                        {
                            "side": "sell",
                            "client_id": cid,
                            "symbol": self.symbol,
                            "qty": qty_str,
                            "max_slippage_bps": 250,
                        }
                    )

            return {"type": "reply", "seq": seq, "orders": orders}

        # 2) Hledání vstupu do longu
        if self.trading_halted or seq >= 1425:
            return {"type": "reply", "seq": seq, "orders": []}

        if len(self.closes) < self.slow_period or (seq - self.last_exit_seq) < self.cooldown_bars:
            return {"type": "reply", "seq": seq, "orders": []}

        if self.ema_fast is None or self.ema_slow is None or close_price <= 0:
            return {"type": "reply", "seq": seq, "orders": []}

        round_trip_cost_ratio = (Decimal("2") * (self.fee_bps + self.slippage_bps)) / Decimal("10000")
        min_edge_ratio = round_trip_cost_ratio + Decimal("0.0005")

        trend_spread = (self.ema_fast - self.ema_slow) / self.ema_slow
        atr_ratio = atr / close_price

        c_4_ago = self.closes[-5]
        mom_4 = (close_price - c_4_ago) / c_4_ago if c_4_ago > 0 else Decimal("0")

        trend_ok = trend_spread >= min_edge_ratio
        momentum_ok = Decimal("0.0010") <= mom_4 <= Decimal("0.0250")
        above_fast_ok = close_price >= self.ema_fast
        volatility_ok = Decimal("0.0003") <= atr_ratio <= Decimal("0.0220")

        if trend_ok and momentum_ok and above_fast_ok and volatility_ok:
            notional = self._compute_buy_notional(depth_quote, fallback_mode=False)
            if notional >= self.min_notional_quote:
                cid = self._next_client_id("buy", seq)
                self.pending_order_id = cid
                self.pending_side = "buy"
                self.pending_since_seq = seq
                orders.append(
                    {
                        "side": "buy",
                        "client_id": cid,
                        "symbol": self.symbol,
                        "notional_quote_units": str(notional),
                        "max_slippage_bps": 250,
                    }
                )

        return {"type": "reply", "seq": seq, "orders": orders}


def main() -> None:
    bot = DeterministicBtcBot()
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        msg = json.loads(line)
        msg_type = msg.get("type")
        if msg_type == "hello":
            bot.on_hello(msg)
        elif msg_type == "batch":
            reply = bot.on_batch(msg)
            sys.stdout.write(json.dumps(reply, separators=(",", ":")) + "\n")
            sys.stdout.flush()
        elif msg_type == "done":
            break


if __name__ == "__main__":
    main()
