# BTC 24h race bot

A long-only BTC momentum bot entered in make.fun's 24-hour race
(`btc-race-24h-20261123-0511`). It trades one symbol, CBBTC, from
one-minute bars: an EMA(9/36) trend filter with ATR-based sizing, a 0.70 %
stop-loss, a 0.38 % trailing stop and a 1.90 % take-profit. One position at
a time, no shorts, no leverage, cash earns nothing.

[![rank](https://make.fun/badge/btc-race-24h-20261123-0511/banonnoe/btc-race-20261123/rank.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511)
[![reward](https://make.fun/badge/btc-race-24h-20261123-0511/banonnoe/btc-race-20261123/reward.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511)
[![bounty](https://make.fun/badge/btc-race-24h-20261123-0511.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511)

## Run

    python3 main.py

The bot reads the protocol's batches on stdin and writes its orders on stdout.
It needs only the Python standard library.

## How it decides

- Enters long when the fast EMA (9) is above the slow EMA (36) and price holds
  above the slow EMA, sizing the order to at most 65 % of equity.
- Exits on a 0.70 % stop-loss, a 0.38 % trailing stop once the trade is 0.30 %
  in profit, or a 1.90 % take-profit; a 4-bar minimum hold and a 7-bar
  cooldown keep it from churning fees.
- Halts new entries after a 15 % drawdown from peak equity.

## License

MIT, see [LICENSE](LICENSE).
