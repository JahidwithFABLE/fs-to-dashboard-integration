# Scenathon 2026–27 Fair Share dashboard integration

Calculate base and adjusted allowances from the selected dashboard emissions and the percentages released by FABLE. The percentages stay fixed across dashboard selections. Emissions, allowances and gaps are recalculated for each selection. The interactive map uses the results of this calculation.

## 1. Install and load the weights

| File | Action |
|---|---|
| [fair_share.py](fair_share.py) | Loads the released weights and calculates allowances and gaps. Requires Python 3.10+ with no third-party dependencies. |
| [fair_share_weights.json](fair_share_weights.json) | Released production input. Pass this file to `load_weights()`. |
| [fair_share_map.py](fair_share_map.py) | Renders the calculation result as an interactive world map. |
| [fair_share_weights_TEMPLATE.json](fair_share_weights_TEMPLATE.json) | Schema example only. The loader rejects it as a production input. |

`scenathon-2026-fs-37-v1` is the released set for 37 participants. It contains 111 participant–approach rows and the agreed cumulative budget of **40,000 MtCO2e for 2026–2050**. The budget is already stored in `budget_mtco2e`. Do not set it separately in the dashboard.

Call `load_weights()` at startup and reload when FABLE issues a new version. `EQ` is the dashboard code for IEPC. `FS_pct` and `FS_adj_pct` are percentages of the cohort-wide required reduction. Each series sums to 100 per approach. They are not percentages of the budget. Keep the full numeric precision and preserve signed adjusted percentages. The reference metadata describes how the weights were calibrated. It does not select or restrict the live pathway. This module uses percentage schema version 2 and does not accept the older fixed-allowance JSON.

## 2. Pass the selected reporting rows

1. When the dashboard updates, call the backend query that supplies the `REPORTING_AGGREGATE` download for the same Scenathon, date or snapshot, pathway and trade stage. Pass the rows before Excel or CSV export. The live calculation does not read an Excel workbook or an exported snapshot file.
2. Fetch the full 37-participant roster for that selection. Do not limit this query to the countries or regions shown in the dashboard. Provide `country`, `year`, `calcallagrico2e` and `calcalllandco2e`. There must be one row per participant for each of **2030, 2035, 2040, 2045 and 2050**, for 185 calculation rows. If the rows also include `pathway_name_default`, `trade_adj`, `scenathon_id` or `snapshot_id`, they must match the selection. `trade_adj=True` means after trade adjustment.
3. Pass the two emissions fields in **MtCO2e/year**. The module multiplies each annual rate by five years and preserves negative values. It does not count the 2025 opening boundary as another block. Do not multiply by five in the query or convert to Gt before passing the values.
4. Align the platform codes with the FABLE participant IDs. The module accepts the dashboard codes below and returns the canonical FABLE IDs. Confirm these aliases against `country` in the live export, especially the separate `R_ZAF` region and `ZAF` country. Do not include both the canonical code and its alias as separate rows.

| Dashboard `country` | FABLE JSON ID |
|---|---|
| DNK, GRC, GTM | DEN, GRE, GUA |
| IRL, NPL, PRT | IRE, NEP, POR |
| GBR, ZAF, R_ZAF | UK, SAF, R_SAF |

```python
from fair_share import calculate, load_weights

# Startup. Use the path to the deployed release JSON.
weights = load_weights("fair_share_weights.json")

# Dashboard refresh. Reuse the live query behind the report download.
result = calculate(
    indicator_rows=reporting_aggregate_rows,
    weights=weights,
    pathway=selected_pathway_value,      # Raw query value rather than translated label.
    trade_adj=selected_after_trade,      # Boolean from the backend selection.
    scenathon_id=selected_scenathon_id,
    snapshot_id=selected_snapshot_id,    # Use the selected date if it is the query's snapshot key.
)
```

If the file is not released or the live selection is incomplete or invalid, log the specific error and show **Fair Share results are unavailable for this selection**. Do not treat missing emissions as zero, reuse stale output or renormalize over a partial cohort.

### Render the interactive map

Pass the result from `fair_share.calculate()` to the map renderer. Do not pass the weights JSON directly.

```python
from fair_share_map import render_fair_share_map

map_html = render_fair_share_map(result)
```

The default view shows the **adjusted relative emissions gap**. Serve the returned document as `text/html` or load it in an iframe with scripts enabled. Render it again when the dashboard selection changes. No changes to the calculation module or weights JSON are required.

## 3. What the calculation does

| Quantity | Calculation and meaning |
|---|---|
| Participant emissions `E_i` | `5 × sum(agriculture rate + signed land-use rate)` across the five reporting years. This is cumulative 2026–2050. |
| Cohort gap `G` | `sum(E_i across all 37) − agreed budget`. Positive means a reduction is needed. Negative means the cohort is below budget. Keep the sign. |
| Base and adjusted reduction assignments | `G × FS_pct / 100` and `G × FS_adj_pct / 100`, for each participant and approach. |
| Base and adjusted allowances | `E_i − corresponding reduction assignment`. A negative allowance is valid. |
| Signed and relative gaps | `allowance − E_i`, and `100 × signed gap / abs(E_i)`. A negative signed gap means emissions exceed that allowance. The relative gap is null when `abs(E_i) ≤ 10⁻⁹ MtCO2e`. |

`summary` has 37 participant rows. `detail` has 111 rows with both weights, allowances, assignments and gaps. `metadata` records the selected scope, weight version and cohort gap. `reconciliation` contains the six allowance totals. Apply any view filters **after** calculation. The calculation module returns results without rendering the dashboard.

Allowances use the same selected emissions shown in the result. They allocate the cohort's gap rather than assess performance against an independent emissions benchmark. For a positive cohort gap and positive weights, all corresponding participant gaps will be negative. Do not interpret that pattern as independent country-level compliance.

## 4. Verify before deployment

- Compare a test export and backend rows from the same selection. Verify the code aliases and exactly 185 distinct calculation rows. The Excel snapshot is useful for this check only.
- Check that all six allowance totals match the budget. On a pathway or trade-stage change, check that the dashboard fetches the new reporting rows and recalculates the results with the same weights. Check that a missing or duplicate block produces the unavailable state.
- Where the dashboard shows **Cumulative Net AFOLU (2025–2050)**, compare the calculation to that indicator only after confirming the same cohort, trade stage, GHG coverage and integration rule. Here 2025 is the opening boundary and is excluded as a row. Convert Gt to Mt for the comparison. A displayed target of 40 GtCO2e is 40,000 MtCO2e.
