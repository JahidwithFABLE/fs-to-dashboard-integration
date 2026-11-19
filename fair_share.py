"""Calculate Scenathon fair-share allowances from percentages and live emissions.

Python 3.10+, standard library only. Use load_weights() at deployment and
calculate() for each backend selection. The website owns the live query.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any


# 01 | Define the release and reporting contracts

APPROACHES = ("AP", "HR", "EQ")  # EQ is displayed as EQ (IEPC).
PARTICIPANT_COUNT = 37
PERIOD = (2026, 2050)
BLOCK_END_YEARS = (2030, 2035, 2040, 2045, 2050)
LIVE_FIELDS = ("country", "year", "calcallagrico2e", "calcalllandco2e")
# Live dashboard codes that differ from the supplied FABLE participant IDs.
# R_ZAF is the region, not the separate ZAF country participant.
LIVE_CODE_ALIASES = MappingProxyType({
    "DNK": "DEN", "GRC": "GRE", "GTM": "GUA", "IRL": "IRE", "NPL": "NEP",
    "PRT": "POR", "GBR": "UK", "ZAF": "SAF", "R_ZAF": "R_SAF",
})
GAP_ZERO = 1e-9  # Relative gap is undefined at or near zero live emissions.
BUDGET_TOLERANCE = 1e-6  # MtCO2e. Publish values at full precision.
PERCENT_TOLERANCE = 1e-8  # percentage points
SCHEMA_VERSION = 2


@dataclass(frozen=True)
class Weights:
    """Released percentage weights used unchanged for each dashboard selection."""

    version: str
    budget_mtco2e: float
    relative_gap_basis: str
    participant_ids: tuple[str, ...]
    percentages: Mapping[tuple[str, str], tuple[float, float]]


def _label(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string.")
    return value.strip()


def _number(value: Any, name: str, *, allow_string: bool = False) -> float:
    types = (int, float, str) if allow_string else (int, float)
    if isinstance(value, bool) or not isinstance(value, types):
        raise ValueError(f"{name} must be a finite number.")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number.")
    return number


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject a JSON object with duplicate keys instead of taking the last."""
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"Duplicate weights JSON key {key!r}.")
        obj[key] = value
    return obj


# 02 | Load and validate the percentages supplied by FABLE

def load_weights(path: str | Path) -> Weights:
    """Load the released JSON and fail closed if it is incomplete."""
    with Path(path).open(encoding="utf-8") as stream:
        payload = json.load(stream, object_pairs_hook=_unique_object)
    return validate_weights(payload)


def validate_weights(payload: Mapping[str, Any]) -> Weights:
    """Validate the release with exactly 37 participant IDs and 111 weight pairs."""
    if not isinstance(payload, Mapping) or type(payload.get("schema_version")) is not int or payload["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Expected Fair Share percentage schema_version 2. Fixed-allowance JSON is not supported.")
    if payload.get("status") != "released":
        raise ValueError("Weights are not released. Use the populated FABLE release JSON.")

    version = _label(payload.get("weight_version"), "weight_version")
    budget = _number(payload.get("budget_mtco2e"), "budget_mtco2e")
    if payload.get("allowance_unit") != "MtCO2e":
        raise ValueError("allowance_unit must be MtCO2e.")
    period = payload.get("period")
    if not isinstance(period, Mapping) or (
        period.get("start_year"), period.get("end_year"), period.get("block_end_years")
    ) != (*PERIOD, list(BLOCK_END_YEARS)):
        raise ValueError("Expected 2026–2050 and the five agreed block-ending years.")

    if payload.get("reporting_rate_basis") != "annual-rate":
        raise ValueError("Expected annual-rate inputs in MtCO2e/year, not five-year totals.")
    gap_basis = _label(payload.get("relative_gap_basis"), "relative_gap_basis")
    if gap_basis not in ("absolute-emissions", "signed-emissions"):
        raise ValueError("relative_gap_basis must be absolute-emissions or signed-emissions.")
    reference = payload.get("reference")
    if not isinstance(reference, Mapping):
        raise ValueError("A FABLE reference record is required.")
    for field in ("calibration_emissions_reference", "adjustment_reference"):
        _label(reference.get(field), f"reference.{field}")
    if type(reference.get("food_reference_year")) is not int or reference["food_reference_year"] not in (2020, 2050):
        raise ValueError("reference.food_reference_year must be 2020 or 2050.")

    raw_ids = payload.get("participant_ids")
    if not isinstance(raw_ids, list):
        raise ValueError("participant_ids must be an array of platform codes.")
    roster = tuple(_label(value, "participant_id").upper() for value in raw_ids)
    if len(roster) != PARTICIPANT_COUNT or len(set(roster)) != len(roster):
        raise ValueError(f"Expected {PARTICIPANT_COUNT} distinct participant IDs.")

    # Store percentages at full precision. Base shares are nonnegative.
    # Adjusted shares may be signed after the zero-sum food correction.
    expected = {(pid, approach) for pid in roster for approach in APPROACHES}
    percentages = {}
    rows = payload.get("weights")
    if not isinstance(rows, list):
        raise ValueError("weights must be an array of participant–approach rows.")
    for i, row in enumerate(rows, 1):
        if not isinstance(row, Mapping):
            raise ValueError(f"Weight row {i} must be an object.")
        pid = _label(row.get("participant_id"), f"Weight row {i} participant_id").upper()
        approach = _label(row.get("approach"), f"Weight row {i} approach")
        key = (pid, approach)
        if key not in expected or key in percentages:
            raise ValueError(f"Unexpected or duplicate weight pair {pid}/{approach}.")
        base = _number(row.get("FS_pct"), f"{pid}/{approach} FS_pct")
        if not 0 <= base <= 100:
            raise ValueError(f"{pid}/{approach} FS_pct must be between 0 and 100.")
        percentages[key] = base, _number(row.get("FS_adj_pct"), f"{pid}/{approach} FS_adj_pct")
    if missing := expected - percentages.keys():
        raise ValueError(f"Missing weight pairs: {sorted(missing)}.")

    for method in APPROACHES:
        for column, name in enumerate(("FS_pct", "FS_adj_pct")):
            total = math.fsum(percentages[pid, method][column] for pid in roster)
            if not math.isclose(total, 100, abs_tol=PERCENT_TOLERANCE, rel_tol=0):
                raise ValueError(f"{method} {name} totals {total:.12g}%, not 100%.")
    return Weights(version, budget, gap_basis, roster, MappingProxyType(percentages))


# 03 | Sum the live agriculture and land-use blocks for one selection

def _flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        flag = value.strip().lower()
        if flag in ("true", "yes", "1"):
            return True
        if flag in ("false", "no", "0"):
            return False
    raise ValueError(f"Unrecognized trade_adj flag {value!r}.")


def _emissions(indicator_rows: Iterable[Mapping[str, Any]], weights: Weights,
               *, pathway: str, trade_adj: bool, scenathon_id: str,
               snapshot_id: str) -> dict[str, tuple[float, float]]:
    blocks: dict[str, dict[int, tuple[float, float]]] = {p: {} for p in weights.participant_ids}
    for i, row in enumerate(indicator_rows, 1):
        if not isinstance(row, Mapping):
            raise ValueError(f"Live row {i} must be a mapping.")
        year = _number(row.get("year"), f"Live row {i} year", allow_string=True)
        if not year.is_integer():
            raise ValueError(f"Live row {i} year must be an integer.")
        if year not in BLOCK_END_YEARS:
            continue  # Do not include historical years or the 2025 boundary.
        if missing := (set(LIVE_FIELDS) - row.keys()):
            raise ValueError(f"Live row {i} is missing columns {sorted(missing)}.")

        # Some exports omit selection fields. The backend must prefilter either way.
        # When present, the fields double-check that no other selection was mixed in.
        if "pathway_name_default" in row and str(row["pathway_name_default"]).strip() != pathway:
            raise ValueError(f"Live row {i} belongs to a different pathway.")
        if "trade_adj" in row and _flag(row["trade_adj"]) != trade_adj:
            raise ValueError(f"Live row {i} belongs to a different trade stage.")
        for field, selected in (("scenathon_id", scenathon_id), ("snapshot_id", snapshot_id)):
            if field in row and str(row[field]).strip() != selected:
                raise ValueError(f"Live row {i} has a different {field}.")

        raw_pid = _label(row["country"], f"Live row {i} country").upper()
        pid = LIVE_CODE_ALIASES.get(raw_pid, raw_pid)
        if pid not in blocks:
            raise ValueError(f"Unexpected participant {raw_pid!r} in the live selection.")
        if year in blocks[pid]:
            raise ValueError(f"Duplicate live emissions row for {pid}/{int(year)}.")
        blocks[pid][int(year)] = tuple(
            _number(row[field], f"{pid}/{int(year)} {field}", allow_string=True)
            for field in LIVE_FIELDS[2:]
        )

    missing_blocks = [f"{pid}/{y}" for pid, years in blocks.items() for y in BLOCK_END_YEARS if y not in years]
    if missing_blocks:
        raise ValueError(f"Missing {len(missing_blocks)} live emissions blocks: {', '.join(missing_blocks[:8])}.")
    # Treat each annual reporting rate as representative of the preceding five years.
    return {
        pid: tuple(5 * math.fsum(years[y][col] for y in BLOCK_END_YEARS) for col in (0, 1))
        for pid, years in blocks.items()
    }


# 04 | Apply a share of the selected cohort's budget gap

def _allocate(emissions: float, cohort_gap: float, share_pct: float, basis: str) -> dict[str, Any]:
    reduction = cohort_gap * (share_pct / 100)
    allowance = emissions - reduction
    # allowance - emissions is exactly -reduction. Use that expression directly.
    gap = -reduction
    denominator = abs(emissions) if basis == "absolute-emissions" else emissions
    return {
        "allowance_mtco2e": allowance,
        "reduction_assignment_mtco2e": reduction,
        "gap_mtco2e": gap,
        "relative_gap_pct": 100 * (gap / denominator) if abs(denominator) > GAP_ZERO else None,
        "within_allowance": gap >= 0,
    }


# 05 | Public calculation entry point for the dashboard backend

def calculate(indicator_rows: Iterable[Mapping[str, Any]], weights: Weights,
              *, pathway: str, trade_adj: bool, scenathon_id: str | int,
              snapshot_id: str | int) -> dict[str, Any]:
    """Return metadata, 37 participant summaries and 111 full detail rows.

    Pass backend rows for the selected event, snapshot, pathway and trade stage.
    Use the pathway value from the query, not a translated display label. Read
    the full roster before applying any chart or download filters.
    """
    pathway = _label(pathway, "pathway")
    if not isinstance(trade_adj, bool):
        raise ValueError("Pass trade_adj as a boolean from the backend selection.")
    ids = []
    for value, field in ((scenathon_id, "scenathon_id"), (snapshot_id, "snapshot_id")):
        if isinstance(value, int) and not isinstance(value, bool):
            value = str(value)
        ids.append(_label(value, field))

    # Step 1. Aggregate the full live cohort, then find its signed gap to the budget.
    components = _emissions(indicator_rows, weights, pathway=pathway, trade_adj=trade_adj,
                            scenathon_id=ids[0], snapshot_id=ids[1])
    net_by_pid = {p: math.fsum(pair) for p, pair in components.items()}
    cohort_emissions = math.fsum(net_by_pid.values())
    cohort_gap = cohort_emissions - weights.budget_mtco2e
    summary, detail = [], []
    for pid in weights.participant_ids:
        agriculture, land_use = components[pid]
        net = net_by_pid[pid]
        summary.append({"participant_id": pid, "agriculture_mtco2e": agriculture,
                        "land_use_mtco2e": land_use, "net_afolu_mtco2e": net})

        # Step 2. Assign a share of the gap and compute each allowance. Keep
        # negative emissions, rights and adjustments signed, including under-budget runs.
        for approach in APPROACHES:
            base_pct, adjusted_pct = weights.percentages[pid, approach]
            detail.append({
                "participant_id": pid, "approach": approach, "net_afolu_mtco2e": net,
                "FS_pct": base_pct, "FS_adj_pct": adjusted_pct,
                "base": _allocate(net, cohort_gap, base_pct, weights.relative_gap_basis),
                "adjusted": _allocate(net, cohort_gap, adjusted_pct, weights.relative_gap_basis),
            })

    # Step 3. Check the budget for each approach and variant before display.
    reconciliation = []
    for approach in APPROACHES:
        group = [r for r in detail if r["approach"] == approach]
        for variant in ("base", "adjusted"):
            total = math.fsum(r[variant]["allowance_mtco2e"] for r in group)
            tolerance = max(BUDGET_TOLERANCE, abs(cohort_gap) * PERCENT_TOLERANCE / 100,
                            1e-12 * math.fsum(abs(r[variant]["allowance_mtco2e"]) for r in group))
            if not math.isclose(total, weights.budget_mtco2e, abs_tol=tolerance, rel_tol=0):
                raise ValueError(f"{approach} {variant} allowances do not reconcile to the configured budget.")
            reconciliation.append({"approach": approach, "variant": variant, "allowance_total_mtco2e": total})
    return {
        "metadata": {"weight_version": weights.version, "budget_mtco2e": weights.budget_mtco2e,
                     "period_start": PERIOD[0], "period_end": PERIOD[1], "pathway": pathway,
                     "scenathon_id": ids[0], "snapshot_id": ids[1], "trade_adj": trade_adj,
                     "selected_cohort_emissions_mtco2e": cohort_emissions,
                     "required_reduction_mtco2e": cohort_gap},
        "summary": summary,
        "detail": detail,
        "reconciliation": reconciliation,
    }
