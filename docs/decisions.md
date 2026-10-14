# Decisions

Short records of choices that shape behaviour. Add a new entry instead of rewriting an old one.

### D1 · FIFO for profit and loss and for the tax (2026-10-14)
FIFO per (broker, asset), both for P&L and for the meerwaardebelasting. The law uses FIFO. An average-cost view
was computed once for comparison: realized profit differed by about 0.5%.

### D2 · Crypto withdrawals are transfers to the owner's wallet (2026-10-14)
Sander's withdrawals went to his Ledger and came back later. Treating them as sales would be wrong; dropping
the coins would lose their purchase price. Coins stay in the pool; returns are matched. Setting:
`own_wallet_withdrawals` (on by default).

### D3 · Zero cost when the purchase price is unknown (2026-10-14)
Staking rewards, airdrops and coins deposited from elsewhere have no price in the export. They count at zero
cost (conservative), with a warning. For the tax, pre-2026 units of these use the 31-12-2025 price anyway.

### D4 · Conversions detected from the data (2026-10-14)
A same-day pair (sell ISIN A, buy ISIN B, same quantity, opposite cash, no costs) is a conversion, not a sale.
Found for Royal Dutch Shell → Shell plc and the Cassava warrant exercise. Setting: `detect_conversions`.

### D5 · Bonds that go to zero on maturity are redeemed at par (2026-10-14)
DEGIRO books a maturity as a sale at price 0. Without the account statement the payout is unknown; 100% of the
nominal is the normal case. Replace with the real payout once the account statement is parsed (backlog 1).

### D6 · LYNX amounts in EUR come from the statement itself (2026-10-14)
Per currency block, scale = the statement's `Total in EUR` / local total. This matches IBKR's EUR figures and
needs no exchange-rate lookups. ECB rates are only a fallback (prices, reference values).

### D7 · Purchase value for the tax: per lot, without costs (2026-10-14)
`unit_acquisition` applies max(31-12-2025 price, original price), capped at the sale price, per FIFO lot; the
original price excludes costs. See docs/domain/meerwaardebelasting.md for the open FIFO-vs-average question.

### D8 · One price per ISIN across brokers (2026-10-14)
The same ETF held at two brokers uses the same price (the newest of statement and online). Earlier dashboards
showed slightly different prices for the same ISIN, which looked wrong.

### D9 · Foreign cash as a currency position (2026-10-14)
LYNX's USD cash is a position of law type "currency". P&L uses IBKR's cost basis; the tax uses the
31-12-2025 rate for what was held then and the receipt rate for 2026 inflows.

### D10 · Streamlit for the app, PDF parsing without poppler (2026-10-14)
Streamlit fits a local, single-user tool and a Python owner. pdfplumber alone (no poppler/pdftotext) keeps
installation and the Docker image simple.

### D11 · Price sources: override > statement > online (2026-10-14)
Every price carries date and source. Online sources are unofficial where no official API exists (justETF,
Yahoo); the user can always override.
