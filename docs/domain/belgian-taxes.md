# Other Belgian taxes around investing

Researched 14 October 2026. Needed for backlog items 2 (dividend tax), 5 (yearly report), 7 (switch planner),
9 (Reynders).

## Stock exchange tax (TOB / beurstaks)

Due on both the purchase and the sale. Rates for ETFs:

| ETF registered | Distributing | Accumulating | Cap per transaction |
|---|---|---|---|
| In Belgium (FSMA list) | 0.12% | **1.32%** | €1,300 (0.12%), €4,000 (1.32%) |
| EEA, not Belgium | 0.12% | 0.12% | €1,300 |
| Outside the EEA | 0.35% | 0.35% | €1,600 |

If one share class of a sub-fund is registered in Belgium, all its share classes count as registered.
Shares: 0.35% (cap €1,600). LYNX withholds TOB and lists it per trade ("BE Securities Transaction Tax");
DEGIRO withholds it for Belgian clients.

Examples (October 2026): IWDA, SSAC/ISAC, SPYY, IMIE, SWRD, CSPX: 0.12%. VWCE and VGLA: 1.32% (registered).
Source: https://curvo.eu/article/tob, https://curvo.eu/article/best-world-etf-belgium

## Dividends and interest

- Belgian withholding (roerende voorheffing) is 30%. Foreign brokers (DEGIRO, LYNX/IBKR) don't withhold it, so
  dividends and interest are declared in the tax return.
- Dividend exemption: dividends up to €833 per person per year (income 2025; same basis for 2026) are exempt,
  max €249.90 tax. It covers dividends of **shares only**: distributions of investment funds, including ETFs
  (trackers), are excluded. Source: Belfius
  https://www.belfius.be/retail/nl/sleutelmomenten/belastingen/dagelijkse-fiscaliteit/fiscaliteit-beleggingen/informatie/dividend/index.aspx
  and Crelan https://www.crelan.be/nl/particulieren/sparen-en-beleggen/beleggen/artikel/belastingvrijstelling-dividenden-faq
- Accumulating ETFs: no dividend tax (nothing is paid out); the reinvested income only meets the 10%
  meerwaardebelasting on sale.
- Irish-domiciled ETFs lose 15% US withholding inside the fund on US dividends; that applies to both distributing
  and accumulating classes.

## Reynders tax

Funds (including ETFs) with more than 10% in debt claims: 30% on the interest part (TIS, taxable income per
share) when units are sold. The 10% meerwaardebelasting applies to the rest of the gain. TIS per share comes from
the fund provider. Relevant positions today: iShares $ Corp Bond (IE0032895942), iShares EUR Covered Bond
(IE00B3B8Q275).

## Foreign accounts

Belgian residents must report accounts at foreign institutions (DEGIRO's Dutch branch, LYNX via Interactive
Brokers Ireland, Bitvavo) to the National Bank's central contact point (CAP) and tick the foreign-account box in
the tax return. Not computed by the app; part of backlog item 5 as a checklist.
