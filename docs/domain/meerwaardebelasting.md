# Meerwaardebelasting (Belgian capital gains tax on financial assets)

Status as researched on 14 October 2026. Re-check sources before relying on a detail that matters.

## Law

- Approved by the Chamber on 3 April 2026, published in the Belgisch Staatsblad on 21 April 2026,
  applies to gains realized from **1 January 2026**. Administrative circular 2026/C/74 clarifies details.
- Three regimes:
  1. Internal capital gains (shares sold to a company you control): 33%. **Not implemented.**
  2. Substantial interest (≥ 20% of a company): €1,000,000 exemption per 5 years, then 1.25–10%. **Not implemented.**
  3. **Other financial assets** (andere financiële activa): **10%**, this is what the app implements.

## Rules implemented (regime 3)

| Topic | Rule | Where in code |
|---|---|---|
| Rate | 10% of the net gain of the tax year above the exemption | `TaxRules.rate`, `TaxCalculator.summarize` |
| Exemption | €10,000 per person per year (2026), indexed. Unused exemption carries forward €1,000/year for 5 years, to at most €15,000 (first possible in 2027). Partners each have their own. Must be claimed in the tax return. | `TaxRules.exemption` (own amount of the tax year), `engine/household.py` (carry-forward, partner), see below |
| Asset types | Financial instruments (shares, bonds, funds, ETFs, derivatives), insurance contracts (tak 21/23/26), crypto-assets, currency, investment gold. All in regime 3. | `models.law_type`, `LAW_TYPES` |
| Loss offset | Losses offset gains **within the same regime and year**, so across all asset types above. No carry-over to later years. Losses before 2026 don't count. | `summarize` nets all lines |
| Reference value | Assets held before 1 January 2026: purchase value = value on **31-12-2025** (closing price; for crypto the price at 23:59 on 31 December). | `TaxRules.reference_date`, `PriceBook.reference` |
| Higher original price | If the provable original purchase price is higher than the 31-12-2025 value, it can be used instead **for sales until 31-12-2030**, but only to reduce a gain (gain floored at 0). A loss is always measured from the 31-12-2025 value. | `unit_acquisition` |
| After 2030 | Only the 31-12-2025 value. | `TaxRules.original_price_until` |
| Bought from 2026 | Real purchase price. | `unit_acquisition` |
| Matching | FIFO (first in, first out). | lot order in `Portfolio` |
| Costs | Commissions, stock exchange tax and other costs are **not** part of purchase or sale value. | `Lot.gross_eur_unit`, `RealizedRow.gross_proceeds_eur` |
| Foreign currency | Purchase and sale converted to EUR at the rate of each transaction date; for the reference value the institution's 31-12-2025 closing rate may be used. | LYNX statement rates, ECB fallback |
| Transfers between own wallets | Not a realization. | `own_wallet_withdrawals` |
| Proof missing | If the original purchase value can't be proven it is deemed zero (circular calls this a last resort, mainly for assets bought after 2025). For pre-2026 assets the 31-12-2025 value can always be used. | unknown 2026 lots → `zero_cost_when_unknown` |
| Speculative crypto | Ordinary investors stay at 10%; 33% only with a combination of factors (share of crypto in wealth, borrowing, trading bots). | not modelled |
| Withholding | Belgian banks withhold (opt-out); foreign brokers don't, so gains are declared in the tax return (income year 2026 = aanslagjaar 2027). | n/a |

## Exemption over the years and partners (implemented, backlog item 4)

- **Own amount per year**: €10,000 for 2026; indexed every year. Later amounts aren't published yet: the user enters
  them (app: Tax years tab; CLI: profile `years.<y>.exemption_base` or `--exemption`). Until then the latest known
  amount is used (`ExemptionRules.base_for`).
- **Carry-forward**: if the exemption isn't (fully) used, the unused part, at most €1,000, is added to the following
  year; each addition can be used for 5 years, so the exemption reaches at most €15,000 (10,000 + 5 × 1,000). Belfius
  says the carried amount is used first the next year; the app uses carried amounts first, oldest first, so the own
  amount is what carries forward again (`exemption_schedule`).
- **Open point (setting `carry_rule`)**: Belfius describes the carry-forward as "the unused balance, max €1,000"
  (`partial`, the default). Other sources (SBB, Kovag) say "if you don't use it, it grows by €1,000 a year", which
  could mean only a year without any gain counts (`none_used`). Check against the circular / the 2027 tax return.
- **Which net gain counts**: per person, the year's sales from the exports (their ownership share) plus other sales
  entered in the profile; once a return is filed, the declared figure can be entered and replaces the calculated one.
- **Partners**: each person has their own exemption. Married couples under the legal regime can each use theirs on
  half of the common assets (Belfius, SBB, Bolero: €20,000 for a couple, up to €30,000 with carry-forward). The app
  splits every line by ownership share (per broker, with exceptions per position) and computes gains, losses,
  exemption and tax per person; one partner's losses don't offset the other's gains. The simulator's "max without
  tax" respects every owner's room: for a share s, the position's room is min(room_p / s_p).

Sources (consulted 14 October 2026): Belfius (https://www.belfius.be/retail/nl/producten/sparen-beleggen/info-publicaties/meerwaardebelasting/index.aspx),
Bolero (https://www.bolero.be/nl/lp/meerwaardebelasting/vrijstelling-en-minderwaarde),
SBB (https://www.sbb.be/nl/magazine/meerwaardebelasting-aandelen),
Kovag (https://kovag.be/nieuws/de-meest-gestelde-vragen-over-de-meerwaardebelasting).
The FOD Financiën page (https://fin.belgium.be/nl/particulieren/belastingaangifte/inkomsten/meerwaardebelasting)
couldn't be read automatically; read it by hand.

## Year-end harvesting (implemented, backlog item 6: `engine/harvest.py`)

- Selling lots with a gain up to the unused exemption and buying them back raises their purchase value without tax.
  Unused exemption is lost at year end except the carry-forward (€1,000).
- **Anti-abuse**: RSM's analysis of the law: "Ook de toepassing van de algemene antimisbruikbepaling blijft volledig
  mogelijk." No specific sell-and-buy-back rule found. The app tells the user to check with an adviser and suggests a
  similar but different fund as the cleaner option.
  https://www.rsm.global/belgium/nl/insights/wet-tot-invoering-van-een-meerwaardebelasting-op-financiele-activa-belangrijkste-kenmerken-en-praktische-aandachtspunten
- **Realization date** (trade or settlement date) isn't addressed in the sources found: the app advises leaving a few
  trading days before 31 December.
- Losses help only when the year's gains are above the exemption (they offset taxable gain); under the exemption
  they only shrink the room.

## Open questions and choices made

- **FIFO vs average for the historical price.** The published-law summaries (LWB) say FIFO. Some earlier
  draft-era sources (BNP Paribas Fortis, DVD Tax Law) mention the *average* purchase price of pre-2026 units for
  the historical-price option. The app applies the max(31-12-2025, original) rule **per FIFO lot**. The effect is
  usually small (only lots bought above the 31-12 price differ). Revisit if the circular says otherwise.
- **Staking rewards received from 2026**: taxed as movable income (30%) when received (per cryptotax.be); their
  later purchase value for the meerwaardebelasting is probably the value at receipt, but not confirmed. The app
  counts them at zero cost (conservative, small amounts).
- **Currency**: gains on foreign cash (LYNX USD) are in scope as "currency". Included as a position.
- **Manual positions** (backlog item 8, `manual.py`, D24): funds, insurance contracts, savings and gold entered by
  hand go through the same FIFO and `unit_acquisition`. Open point: for **tak 21** policies (and tak 23 with a
  guaranteed part) held less than 8 years, the return is already subject to 30% roerende voorheffing on surrender;
  how that interacts with the 10% (whether the part already taxed is excluded) wasn't checked. The app counts the full
  gain; check with an adviser before relying on it. Savings in euro give no gain (currency = EUR, price 1).
- **Bond funds**: Reynders tax (30% on the bond part, TIS) applies on top; the 10% applies to the rest of the
  gain. Not implemented (backlog item 9).

## Sources

- LWB, law published in the Staatsblad: https://www.lwb-info.be/nieuws/meerwaardebelasting-is-definitief-wet-verschenen-in-belgisch-staatsblad-377/
- KPMG, approved by parliament (regimes, categories, 3 April 2026): https://kpmg.com/be/nl/insights/my-tax-compass-de-trends-die-op-uw-fiscale-radar-moeten-staan/people-services-updates/meerwaardebelasting-goedgekeurd-door-het-parlement.html
- Vandelanotte, circular 2026/C/74: https://www.vandelanotte.be/nl/nieuws/meerwaardebelasting-dit-verduidelijkt-de-nieuwe-circulaire
- Argenta summary (exemption, 31-12-2025 snapshot, covered assets): https://www.argenta.be/nl/argenta-informeert/meerwaardebelasting.html
- DVD Tax Law, crypto (wallet transfers, 23:59 price, loss offset across types): https://www.dvdtaxlaw.be/meerwaardebelasting-crypto-2026/
- Puilaetco, asset list and regimes (draft-era): https://www.puilaetco.be/nl-be/blog/meerwaardebelasting-op-financiele-activa
- BNP Paribas Fortis, ten questions (draft-era): https://myexperts.bnpparibasfortis.be/nl/vermogen/tien-vragen-over-de-meerwaardebelasting-beantwoord
- Delen, Reynders + 10% on bond funds: https://www.delen.bank/nl-be/blog/de-meerwaardebelasting-wat-u-moet-weten
- Calculation rules of an independent calculator (FIFO, costs excluded): https://www.meerwaardebelastingberekenen.be/calculation-rules
- Staking: https://cryptotax.be/kennis/staking-belasting-belgie
