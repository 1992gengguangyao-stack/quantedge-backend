# BTCquant v0.1.0 Research Preview

BTCquant is a wallet-first Bitcoin strategy research workspace. The current preview is designed to help a trader move from a written hypothesis to a fee-aware historical test, then validate locally signed Hyperliquid actions on testnet before independently deciding whether any live risk is appropriate.

## Evaluate it in 15 minutes

1. Use the [free fee and slippage calculator](https://aiquantbtc.com/crypto-backtest-fee-calculator?utm_source=github&utm_medium=docs&utm_campaign=v0_1_0) to estimate the gross return your turnover must clear.
2. Read the [Bitcoin strategy playbook](https://aiquantbtc.com/bitcoin-quant-strategies?utm_source=github&utm_medium=docs&utm_campaign=v0_1_0) and write one falsifiable strategy hypothesis.
3. Open the [Free workspace](https://aiquantbtc.com/dashboard?utm_source=github&utm_medium=docs&utm_campaign=v0_1_0), connect a wallet with an off-chain SIWE message, and save the hypothesis.
4. Run a backtest with fees and slippage enabled. Inspect drawdown and trade count rather than only total return.
5. If testing Hyperliquid, use the [L1 signing guide](https://aiquantbtc.com/hyperliquid-l1-signing?utm_source=github&utm_medium=docs&utm_campaign=v0_1_0) and keep the workflow on testnet.

## Current enforced research limits

| Plan | Saved strategies | Backtests per UTC day | Saved bot configurations |
| --- | ---: | ---: | ---: |
| Free | 3 | 3 | 1 |
| Starter | 15 | 25 | 10 |
| Pro | 100 | 200 | 50 |
| Expert | 500 | 1,000 | 200 |

Paid access increases research capacity. It does not change the risk of a strategy or guarantee a profitable result.

## Security boundary

- Wallet login signs an off-chain message; it is not a transfer.
- The marketed Hyperliquid workflow is testnet-first and signs locally in the browser.
- BTCquant should never receive a seed phrase or main-wallet private key.
- BTCquant does not custody customer trading funds.
- USDT subscription checkout uses TRC-20 and activates only after exact-amount on-chain confirmation.

## What is not part of this preview

- Managed accounts or custody
- Guaranteed returns or guaranteed risk reduction
- Active copy-trading or creator payouts
- A hidden testnet-to-mainnet fallback
- A claim that historical performance predicts future results

## Feedback

Please [start a GitHub discussion](https://github.com/1992gengguangyao-stack/quantedge-backend/discussions/new?category=general) with the first point that felt unclear: wallet connection, strategy setup, cost assumptions, backtest output, or testnet signing.

For annual research capacity, see the [Founding 9 access page](https://aiquantbtc.com/founding-nine?utm_source=github&utm_medium=docs&utm_campaign=v0_1_0). Cryptocurrency trading and protocol use can result in substantial loss. BTCquant is research software, not financial advice.
