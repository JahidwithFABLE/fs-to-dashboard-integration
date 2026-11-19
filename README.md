# Scenathon 2026-27 Fair Share calculation

Calculate base and adjusted allowances from the selected dashboard emissions and the percentages released by FABLE. The percentages stay fixed across dashboard selections. Emissions, allowances and gaps are recalculated for each selection. The interactive map uses the results of this calculation.

## 1. Install and load the weights

| File | Action |
|---|---|
| `fair_share.py` | Put this module in the calculation layer. It uses Python 3.10+ with no third-party dependencies. |
| `fair_share_weights_TEMPLATE.json` | Use this only to inspect the input format. Load the populated `fair_share_weights.json` supplied by FABLE when it is released. |
| `fair_share_map.py` | Use this in the display layer to render the interactive world map from the calculation result. |

Call `load_weights()` at startup, and reload when FABLE issues a new version. The file has 38 participant IDs and 114 rows, one per participant and approach. `EQ` is the dashboard code for IEPC. `FS_pct` and `FS_adj_pct` are percentages of the cohort-wide required reduction. Each series sums to 100 per approach. They are not percentages of the budget. Keep the full numeric precision and preserve signed adjusted percentages. The JSON also provides the agreed budget in MtCO2e, the period, the rate basis and reference metadata. The metadata explains how the weights were calibrated. It does not select or restrict the live pathway.

The template has no approved weight values. Its 40,000 MtCO2e budget is provisional and the loader will reject it as a production input. This module uses percentage schema version 2 and does not accept the older fixed-allowance JSON.

## 2. Pass the selected reporting rows

1. When the dashboard updates, call the backend query that supplies the `REPORTING_AGGREGATE` download for the same Scenathon, date or snapshot, pathway and trade stage. Pass the rows before Excel or CSV export. No workbook or uploaded snapshot is needed in the live workflow.
2. Fetch the full 38-participant roster for that selection. Do not limit this query to the countries or regions shown in the dashboard. Provide `country`, `year`, `calcallagrico2e` and `calcalllandco2e`. There must be one row per participant for each of **2030, 2035, 2040, 2045 and 2050**, for 190 calculation rows. If the rows also include `pathway_name_default`, `trade_adj`, `scenathon_id` or `snapshot_id`, they must match the selection. `trade_adj=True` means after trade adjustment.
3. Pass the two emissions fields in **MtCO2e/year**. The module multiplies each annual rate by five years, and keeps signed land-use emissions. It does not count the 2025 opening boundary as another block. Do not multiply by five in the query or convert to Gt before passing the values.
4. Align the platform codes with the FABLE participant IDs. The module accepts the dashboard codes below and returns the canonical FABLE IDs. Confirm these aliases against `country` in the live export, especially the separate `R_ZAF` region and `ZAF` country. Do not include both the canonical code and its alias as separate rows.

| Dashboard `country` | FABLE JSON ID |
|---|---|
| DNK, GRC, GTM | DEN, GRE, GUA |
| IRL, NPL, PRT | IRE, NEP, POR |
| GBR, ZAF, R_ZAF | UK, SAF, R_SAF |

```python
from fair_share import calculate, load_weights

# Startup. Use the released FABLE file, not the template.
weights = load_weights(weights_path)

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
| Cohort gap `G` | `sum(E_i across all 38) − agreed budget`. Positive means a reduction is needed. Negative means the cohort is below budget. Keep the sign. |
| Base and adjusted reduction assignments | `G × FS_pct / 100` and `G × FS_adj_pct / 100`, for each participant and approach. |
| Base and adjusted allowances | `E_i − corresponding reduction assignment`. A negative allowance is valid. |
| Signed and relative gaps | `allowance − E_i`, and `100 × signed gap / abs(E_i)`. A negative signed gap means emissions exceed that allowance. The relative gap is null when `abs(E_i) ≤ 10⁻⁹ MtCO2e`. |

`summary` has 38 participant rows. `detail` has 114 rows with both weights, allowances, assignments and gaps. `metadata` records the selected scope, weight version and cohort gap. `reconciliation` contains the six allowance totals. Apply any view filters **after** calculation. The calculation module returns results without rendering the dashboard.

Allowances use the same selected emissions shown in the result. They allocate the cohort's gap rather than assess performance against an independent emissions benchmark. For a positive cohort gap and positive weights, all corresponding participant gaps will be negative. Do not interpret that pattern as independent country-level compliance.

## 4. Verify before deployment

- Compare a test export and backend rows from the same selection. Verify the code aliases and exactly 190 distinct calculation rows. The Excel snapshot is useful for this check only.
- Check that all six allowance totals match the budget and that changing pathway or trade stage changes live emissions and derived allowances without changing either weight column. Check that a missing or duplicate block produces the unavailable state.
- Where the dashboard shows **Cumulative Net AFOLU (2025–2050)**, compare the calculation to that indicator only after confirming the same cohort, trade stage, GHG coverage and integration rule. Here 2025 is the opening boundary and is excluded as a row. Convert Gt to Mt for the comparison. A displayed target of 40 GtCO2e is 40,000 MtCO2e.
