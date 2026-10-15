# Other Belgian taxes around investing

Researched 14 October 2026. Used by the dividend tax (item 2) and the yearly report (item 5); needed later for
items 7 (switch planner) and 9 (Reynders).

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

Examples (October 2026): IWDA, SPYY, IMIE, SWRD, CSPX: 0.12%. VWCE and VGLA: 1.32% (registered).
Source: https://curvo.eu/article/tob, https://curvo.eu/article/best-world-etf-belgium

**Correction from real data (DEGIRO account statement, 12-10-2026):** a purchase of iShares MSCI ACWI
(IE00B6R52259, ISAC/SSAC) was charged **1.32%**. So ISAC counts as Belgian-registered
accumulating at DEGIRO; the 0.12% listed earlier for it was wrong. Check a fund's actual rate before a switch.
Same data: VWCE (IE00BK5BQT80) was charged 0.12% on purchases until 2024 and **1.32%** on a 2026 sale: rates change when a fund (or a sibling share class) gets registered in Belgium.

## Dividends and interest (implemented, backlog item 2: `engine/income_tax.py`)

- Belgian withholding (roerende voorheffing) is 30%. Foreign brokers (DEGIRO, LYNX/IBKR) don't withhold it, so
  dividends and interest are declared in the tax return (box VII, income received without Belgian withholding).
- The 30% applies to the amount received **after foreign withholding tax**; the foreign tax isn't credited
  (France has a special regime, not modelled). LYNX withholds 20% Irish tax on credit interest.
- Dividend exemption per person, dividends of **shares only** (not funds, bevaks, ETFs/trackers):

  | Income year | Exempt dividends | Max tax saved |
  |---|---|---|
  | 2023 | €800 | €240 |
  | 2024 | €833 | €249.90 |
  | 2025 | €833 | €249.90 (Triodos, KBC; Wolters Kluwer's own calculation said €859) |
  | 2026 | €833 | €249.90 (Belfius) |

  `DIVIDEND_EXEMPTION`; the profile can override a year. When Belgian withholding was applied (Belgian bank) the
  exemption is reclaimed with code 1437/2437; for foreign income the dividends are declared and the exemption is
  claimed in the same box. **Exact codes for the 2027 return aren't published yet: check Tax-on-web.**
  Sources: Belfius (link below), KBC https://www.kbc.be/particulieren/nl/juridische-info/documentatie-beleggen/vrijstelling-roerende-voorheffing-dividenden-aandelen.html,
  Triodos https://www.triodos.be/nl/meest-gestelde-vragen/wat-houdt-de-vrijstelling-van-belgische-roerende-voorheffing-voor-dividenden-van-aandelen-in?id=080d234b81ba,
  Wikifin https://www.wikifin.be/nl/belasting-werk-en-inkomen/belastingaangifte/fiscale-voordelen-bij-beleggingen/terugvorderen-van,
  Wolters Kluwer https://assets.contenthub.wolterskluwer.com/api/public/content/2572308-fiscale-grensbedragen-2026-946402cb1f
- Interest on a broker's cash account: 30%, no exemption (the savings exemption is for regulated savings deposits).
- The app classifies income by the paying asset's class: `Stock` → shares (exemption), anything else → funds.
  LYNX dividends get their class from the statement's instrument type.
- Earlier source: Belfius
  https://www.belfius.be/retail/nl/sleutelmomenten/belastingen/dagelijkse-fiscaliteit/fiscaliteit-beleggingen/informatie/dividend/index.aspx
  and Crelan https://www.crelan.be/nl/particulieren/sparen-en-beleggen/beleggen/artikel/belastingvrijstelling-dividenden-faq
- Accumulating ETFs: no dividend tax (nothing is paid out); the reinvested income only meets the 10%
  meerwaardebelasting on sale.
- Irish-domiciled ETFs lose 15% US withholding inside the fund on US dividends; that applies to both distributing
  and accumulating classes.

## Reynders tax (implemented, backlog item 9: `engine/reynders.py`, D25)

Art. 19bis WIB92. Funds (including ETFs) with more than 10% in debt claims (25% for units acquired before
1 January 2018): 30% on the interest part when units are sold or redeemed, no exemption. A foreign broker doesn't
withhold it: declare it in the tax return (Curvo names code 1444, part VII; check it in the return).

| Topic | What sources say | App |
|---|---|---|
| Base | The interest part of the gain: TIS (taxable income per share) at the sale minus TIS at the purchase, published by the fund provider | TIS method, per FIFO lot, each TIS converted to EUR at its date |
| No TIS | Brokers apply an "asset test": the gain × the fund's bond share (Curvo); without information some tax 100% of the gain | asset test with the bond share you enter, else worst case (100% of the gain), flagged |
| Cap | Not settled in the sources found; the residual approach implies a gain is needed | setting `cap_at_gain` (default on): base ≤ real gain of the lot, zero on a loss |
| Distributing funds | In principle outside art. 19bis, but most are taxed in practice because the prospectus doesn't commit to paying out everything (Curvo, Auryth) | your call per fund (`subject`); bond ETFs default to subject in the app |
| With the 10% | Reynders applies first, without exemption; the 10% and its exemption apply to the rest of the gain (Auryth, Geldvos, Delen) | setting `deduct_from_cgt` (default on); the deduction never exceeds a lot's 10% gain |
| Real gain | Sale price − purchase price, without costs; not the 31-12-2025 value | `Lot.gross_eur_unit` |

Units bought before the first TIS you enter count from TIS 0 (worst case within the cap). Year-end harvesting leaves
subject funds out; the switch planner adds their Reynders tax to the cost now and to each year of the spread plan.

Sources (consulted 15 October 2026): Curvo, Reynders taks (https://curvo.eu/nl/artikel/reynders-taks);
Auryth, Meerwaardebelasting en de Reynderstaks (https://auryth.ai/nl/blog/meerwaardebelasting-reynderstaks/);
Geldvos (https://geldvos.com/meerwaardebelasting-belgie-complete-gids/); Belfius
(https://www.belfius.be/retail/nl/producten/sparen-beleggen/info-publicaties/meerwaardebelasting/index.aspx).
TIS values: the fund provider's Belgian tax information (iShares, Vanguard, Amundi publish TIS/BTIS per share).

## Stock exchange tax in the yearly report (backlog item 5)

- LYNX lists the tax per trade in the statement's *Transaction Fees* section ("BE Securities Transaction Tax"); it is
  also inside the trade's commission, so the app keeps these rows as information only (`TOB` kind, `Portfolio.tob`).
  Per-year totals match the statement's NAV line "Transaction Fees" to the cent.
- DEGIRO's account statement lists it per trade ("Transactiebelasting België"); the *Transacties* export doesn't
  include it at all, not even in its costs. Without the account statement the report falls back to an **estimate**
  at 0.12% / 0.35%, which misses the 1.32% of Belgian-registered accumulating funds.
- If a foreign broker doesn't withhold it, the investor files the stock exchange tax return himself, by the last
  working day of the second month after the trade.

## Foreign accounts

Belgian residents must report accounts at foreign institutions (DEGIRO's Dutch branch, LYNX via Interactive
Brokers Ireland, Bitvavo) to the National Bank's central contact point (CAP) and tick the foreign-account box in
the tax return, with the countries. Whether a crypto platform account must be reported to the CAP isn't settled.
The yearly report has this as a checklist item with the institutions and countries (`taxreport.ACCOUNTS`).
