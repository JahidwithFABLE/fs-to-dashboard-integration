# IIASA · Scenathon 2026–27 Fair Share handover

This update freezes the Fair Share allowances from **initial Current Trends before trade adjustment**. A dashboard selection changes the emissions and the resulting gaps. It does not change the benchmark or the weights.

| File | Role |
| --- | --- |
| [fair_share.py](fair_share.py) | Includes and validates the frozen reference for all 38 participants and 114 participant–approach pairs, with Base and Adjusted allowances. Python 3.10+, standard library only. |
| [fair_share_weights.json](fair_share_weights.json) | Unchanged released AP, HR and EQ (IEPC) weights and 40,000 MtCO₂e budget. |
| [fair_share_map.py](fair_share_map.py) | Same renderer interface and map design. Labels identify the fixed reference and the selected pathway. |

## 1. Load the existing weights and bundled benchmark at startup

```python
from pathlib import Path

from fair_share import calculate, load_benchmark, load_weights
from fair_share_map import render_fair_share_map

# Set this to the deployed directory containing the weights JSON.
release_dir = Path(".")
weights = load_weights(release_dir / "fair_share_weights.json")
benchmark = load_benchmark(weights=weights)  # Uses the released benchmark bundled in fair_share.py.
```

Deploy the updated Python modules with the existing released weights JSON. The weights version is `scenathon-2026-fs-38-v1`. The benchmark version is `scenathon-2026-ct-pretrade-38-v1`. Do not regenerate the benchmark from a dashboard selection or overwrite it with a newly downloaded Current Trends export.

The benchmark uses FABLE's corrected initial Current Trends submissions before trade adjustment for Scenathon **9**. Its full-roster 2026–2050 net AFOLU is **298,934.92424768 MtCO₂e**. The required reduction at calibration is that total minus the 40,000 MtCO₂e budget. All six stored allocation totals reconcile to the budget. The benchmark's `snapshot_id` is a FABLE provenance identifier, not an IIASA database key. The artifact was extracted and numerically reconciled against the corrected local calibration audit used to release the weights. It was not made from the incomplete 19 November dashboard preview.

## 2. Read live emissions for the dashboard selection

Use the backend rows that supply the **Indicators** sheet in the Scenathon snapshot export. Pass them before any aggregation or formatting into a chart. You do not need to read an Excel workbook in the dashboard.

- Query the complete **38-participant cohort** for one selected event, snapshot, pathway and trade stage. Do not apply country or region display filters to this query.
- Required columns are `country`, `year`, `calcallagrico2e` and `calcalllandco2e`. The expected horizon is **190 rows** with one row per participant in each of 2030, 2035, 2040, 2045 and 2050. If present, `pathway_name_default`, `trade_adj`, `scenathon_id` and `snapshot_id` must match the live selection.
- Pass signed emissions rates in **MtCO₂e/year**. The module multiplies each reporting block by five internally. The 2025 opening boundary is excluded.
- The module resolves `DNK → DEN`, `GRC → GRE`, `GTM → GUA`, `IRL → IRE`, `NPL → NEP`, `PRT → POR`, `GBR → UK`, `ZAF → SAF` and `R_ZAF → R_SAF`. Do not send a canonical ID and alias for the same participant-year. The existing map already includes R_CAS (KAZ, KGZ, MNG, TJK, TKM).

```python
# Run when the selected pathway, snapshot or trade stage changes.
result = calculate(
    indicator_rows=selected_reporting_rows,
    weights=weights,
    benchmark=benchmark,
    pathway=selected_pathway,       # Exact backend value
    trade_adj=selected_after_trade,  # Boolean
    scenathon_id=selected_event_id,  # 9 for this release
    snapshot_id=selected_snapshot_id,
)

map_html = render_fair_share_map(result)
```

The change to the dashboard call is the new required **`benchmark=benchmark`** argument. A later pathway, snapshot or trade stage still uses the same benchmark. A different Scenathon requires its own released benchmark.

Missing or duplicate live inputs stop the calculation. Surface an unavailable result and log the cause. Do not substitute zeros, show stale values or rescale the weights over an incomplete cohort. If the corrected submissions have not propagated to the backend, the packaged benchmark is still valid, but the emitted live comparison can only be as good as the selected reporting data. Use a live export containing the full 38-participant roster, including R_CAS. Older 37-participant exports are not compatible with this release.

## 3. Calculations and plotting

At calibration, for each approach and variant:

```text
Current Trends cohort gap = sum(Current Trends net AFOLU) − budget
Fixed participant allowance = Current Trends participant net AFOLU
                              − (released weight / 100 × Current Trends cohort gap)
```

For a dashboard selection:

```text
Selected net AFOLU = 5 × sum(selected agriculture rate + signed land-use rate)
Signed gap = fixed Current Trends allowance − selected net AFOLU
Relative gap (%) = 100 × signed gap / abs(selected net AFOLU)
```

Negative gaps mean selected emissions exceed the allowance. Relative gap is null at or near zero selected net emissions. Negative net emissions and allowances are valid and remain signed.

- **Bar chart or comparison plot:** read selected `net_afolu_mtco2e` for the emissions series and fixed `base.allowance_mtco2e` / `adjusted.allowance_mtco2e` for the reference. An emissions-to-allowance index, if shown, must also use the fixed allowance.
- **Table and map:** read `gap_mtco2e` and `relative_gap_pct` from the result. Do not recalculate the allowance or derive the gap from the reduction assignment. Label the two series **Selected pathway emissions** and **Fixed Current Trends allowance (before trade)**.
- **Regional view:** sum selected emissions and fixed allowances separately before computing regional gaps or indices. The country polygons in the map are not an aggregation crosswalk.
- **Scale:** use the same map scale across pathway comparisons when useful by supplying a common `max_abs`. Styles, colour conventions, layout and the renderer call do not otherwise change.
- **Metadata:** `pathway`, `trade_adj` and `snapshot_id` describe the live selection. The `benchmark_*` fields describe the fixed reference. The existing `required_reduction_mtco2e` is the selected cohort's arithmetic gap to the budget, not an input into the allowances. `baseline_reduction_assignment_mtco2e` and its compatibility field `reduction_assignment_mtco2e` are fixed Current Trends assignments, not the selected pathway gaps. Update any existing tooltip or chart labels that conflate these quantities.

The result retains 38 summary rows, 114 detail rows and six reconciliation rows. Each detail row keeps the existing Base and Adjusted objects. No calculator or trade-adjustment logic changes are needed.

## 4. Verify

On deployment, compare the live reporting rows to a valid selected snapshot. Switch pathway, snapshot or trade stage and verify that the fixed allowance values stay unchanged, while the selected emissions and gaps update. Confirm that all six allowances reconcile to the 40,000 MtCO₂e budget across the full cohort.
