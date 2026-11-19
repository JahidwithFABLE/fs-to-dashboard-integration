"""Compare selected Scenathon emissions with the released fixed FABLE benchmark.

Python 3.10+, standard library only. Load weights and benchmark at startup.
Call calculate() for each backend selection. Allowances are never rebuilt there.
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
PARTICIPANT_COUNT = 38
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
BENCHMARK_SCHEMA_VERSION = 1
BENCHMARK_BASIS = "fixed-current-trends-pretrade"
BENCHMARK_PATHWAY = "Current Trends"


@dataclass(frozen=True)
class Weights:
    """Released percentage weights used unchanged for each dashboard selection."""

    version: str
    budget_mtco2e: float
    relative_gap_basis: str
    participant_ids: tuple[str, ...]
    percentages: Mapping[tuple[str, str], tuple[float, float]]


@dataclass(frozen=True)
class Benchmark:
    """Released initial Current Trends, before-trade allowances."""

    version: str
    weights: Weights
    scenathon_id: str
    snapshot_id: str
    source: str
    cohort_emissions_mtco2e: float
    cohort_gap_mtco2e: float
    net_emissions: Mapping[str, float]
    allowances: Mapping[tuple[str, str], tuple[float, float]]


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
    """Validate the release with exactly 38 participant IDs and 114 weight pairs."""
    if not isinstance(payload, Mapping) or type(payload.get("schema_version")) is not int or payload["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Expected Fair Share weights schema_version 2. Load the benchmark separately.")
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


# 03 | Load the frozen Current Trends calculation once

def _selection_id(value: Any, field: str) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    return _label(value, field)


def _reconcile(allowances: Mapping[tuple[str, str], tuple[float, float]],
               weights: Weights, cohort_gap: float) -> list[dict[str, Any]]:
    rows = []
    for approach in APPROACHES:
        for index, variant in enumerate(("base", "adjusted")):
            values = [allowances[pid, approach][index] for pid in weights.participant_ids]
            total = math.fsum(values)
            tolerance = max(BUDGET_TOLERANCE, abs(cohort_gap) * PERCENT_TOLERANCE / 100,
                            1e-12 * math.fsum(map(abs, values)))
            if not math.isclose(total, weights.budget_mtco2e, rel_tol=0, abs_tol=tolerance):
                raise ValueError(f"Benchmark {approach}/{variant} allowances do not reconcile to the budget.")
            rows.append({"approach": approach, "variant": variant, "allowance_total_mtco2e": total})
    return rows


def validate_benchmark(payload: Mapping[str, Any], weights: Weights) -> Benchmark:
    """Check the artifact against the released weights, roster and baseline formula."""
    if (not isinstance(payload, Mapping) or type(payload.get("schema_version")) is not int
            or payload["schema_version"] != BENCHMARK_SCHEMA_VERSION):
        raise ValueError("Expected benchmark schema_version 1.")
    if payload.get("status") != "released" or payload.get("allowance_basis") != BENCHMARK_BASIS:
        raise ValueError("A released fixed Current Trends benchmark is required.")
    if payload.get("pathway") != BENCHMARK_PATHWAY or payload.get("trade_adj") is not False:
        raise ValueError("The fixed benchmark must be initial Current Trends before trade adjustment.")
    if payload.get("weight_version") != weights.version:
        raise ValueError("Benchmark and weights releases do not match.")
    if payload.get("unit") != "MtCO2e" or (
        payload.get("period_start"), payload.get("period_end")
    ) != PERIOD:
        raise ValueError("Benchmark must contain cumulative 2026–2050 MtCO2e values.")
    if _number(payload.get("budget_mtco2e"), "Benchmark budget") != weights.budget_mtco2e:
        raise ValueError("Benchmark and weights budgets do not match.")
    version = _label(payload.get("benchmark_version"), "benchmark_version")
    event = _selection_id(payload.get("scenathon_id"), "benchmark scenathon_id")
    snapshot = _selection_id(payload.get("snapshot_id"), "benchmark snapshot_id")
    source = _label(payload.get("source"), "benchmark source")

    records = payload.get("reference_afolu_mtco2e")
    if not isinstance(records, Mapping) or set(records) != set(weights.participant_ids):
        raise ValueError("Benchmark emissions must cover the full released roster.")
    emissions = {pid: _number(records[pid], f"Benchmark {pid} AFOLU")
                 for pid in weights.participant_ids}
    cohort_emissions = math.fsum(emissions.values())
    cohort_gap = cohort_emissions - weights.budget_mtco2e
    for name, expected in (("cohort_emissions_mtco2e", cohort_emissions), ("cohort_gap_mtco2e", cohort_gap)):
        actual = _number(payload.get(name), f"Benchmark {name}")
        if not math.isclose(actual, expected, rel_tol=0, abs_tol=BUDGET_TOLERANCE):
            raise ValueError(f"Benchmark {name} does not match the full roster.")

    rows = payload.get("allowances")
    if not isinstance(rows, list):
        raise ValueError("Benchmark allowances must be an array.")
    allowances = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("Expected participant–approach benchmark rows.")
        pid = _label(row.get("participant_id"), "benchmark participant_id")
        approach = _label(row.get("approach"), "benchmark approach")
        key = pid, approach
        if key not in weights.percentages or key in allowances:
            raise ValueError(f"Unexpected or duplicate benchmark row {pid}/{approach}.")
        values = tuple(_number(row.get(f"{variant}_allowance_mtco2e"),
                               f"Benchmark {pid}/{approach}/{variant}")
                       for variant in ("base", "adjusted"))
        for value, share in zip(values, weights.percentages[key]):
            target = emissions[pid] - cohort_gap * (share / 100)
            if not math.isclose(value, target, rel_tol=0, abs_tol=BUDGET_TOLERANCE):
                raise ValueError(f"Benchmark allowance {pid}/{approach} does not match its reference formula.")
        allowances[key] = values
    if allowances.keys() != weights.percentages.keys():
        raise ValueError("Benchmark allowances must have all 114 participant–approach pairs.")
    _reconcile(allowances, weights, cohort_gap)
    return Benchmark(version, weights, event, snapshot, source, cohort_emissions,
                     cohort_gap, MappingProxyType(emissions), MappingProxyType(allowances))


def load_benchmark(path: str | Path | None = None, weights: Weights | None = None) -> Benchmark:
    """Load the bundled reference, or an explicitly supplied benchmark release."""
    if not isinstance(weights, Weights):
        raise ValueError("Pass the released weights to load_benchmark(weights=weights).")
    if path is None:
        payload = json.loads(_BUNDLED_BENCHMARK_JSON, object_pairs_hook=_unique_object)
    else:
        with Path(path).open(encoding="utf-8") as stream:
            payload = json.load(stream, object_pairs_hook=_unique_object)
    return validate_benchmark(payload, weights)


# 04 | Sum the live agriculture and land-use blocks for one selection
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


# 05 | Compare live emissions with a fixed allowance

def _compare(emissions: float, allowance: float, baseline_reduction: float,
             basis: str) -> dict[str, Any]:
    gap = allowance - emissions
    denominator = abs(emissions) if basis == "absolute-emissions" else emissions
    return {
        "allowance_mtco2e": allowance,
        # Compatibility field: the assignment is now the frozen Current Trends assignment.
        "reduction_assignment_mtco2e": baseline_reduction,
        "baseline_reduction_assignment_mtco2e": baseline_reduction,
        "gap_mtco2e": gap,
        "relative_gap_pct": 100 * (gap / denominator) if abs(denominator) > GAP_ZERO else None,
        "within_allowance": gap >= 0,
    }


# 06 | Public calculation entry point for the dashboard backend

def calculate(indicator_rows: Iterable[Mapping[str, Any]], weights: Weights,
              *, benchmark: Benchmark, pathway: str, trade_adj: bool, scenathon_id: str | int,
              snapshot_id: str | int) -> dict[str, Any]:
    """Return metadata, 38 participant summaries and 114 full detail rows.

    Pass the selected reporting rows and the same startup benchmark for every
    pathway or trade-stage selection. Filter the display only after calculation.
    """
    pathway = _label(pathway, "pathway")
    if not isinstance(trade_adj, bool):
        raise ValueError("Pass trade_adj as a boolean from the backend selection.")
    event = _selection_id(scenathon_id, "scenathon_id")
    snapshot = _selection_id(snapshot_id, "snapshot_id")
    if not isinstance(benchmark, Benchmark) or benchmark.weights != weights:
        raise ValueError("Load the released benchmark using the same released weights.")
    if benchmark.scenathon_id != event:
        raise ValueError("The selected Scenathon does not match the fixed benchmark.")

    # Selected emissions are read from the live rows, including for Current Trends.
    components = _emissions(indicator_rows, weights, pathway=pathway, trade_adj=trade_adj,
                            scenathon_id=event, snapshot_id=snapshot)
    net_by_pid = {p: math.fsum(pair) for p, pair in components.items()}
    cohort_emissions = math.fsum(net_by_pid.values())
    cohort_gap = cohort_emissions - weights.budget_mtco2e
    summary, detail = [], []
    for pid in weights.participant_ids:
        agriculture, land_use = components[pid]
        net = net_by_pid[pid]
        reference_net = benchmark.net_emissions[pid]
        summary.append({"participant_id": pid, "agriculture_mtco2e": agriculture,
                        "land_use_mtco2e": land_use, "net_afolu_mtco2e": net,
                        "baseline_net_afolu_mtco2e": reference_net})

        for approach in APPROACHES:
            base_pct, adjusted_pct = weights.percentages[pid, approach]
            base_allowance, adjusted_allowance = benchmark.allowances[pid, approach]
            detail.append({
                "participant_id": pid, "approach": approach, "net_afolu_mtco2e": net,
                "baseline_net_afolu_mtco2e": reference_net,
                "FS_pct": base_pct, "FS_adj_pct": adjusted_pct,
                "base": _compare(net, base_allowance, reference_net - base_allowance, weights.relative_gap_basis),
                "adjusted": _compare(net, adjusted_allowance, reference_net - adjusted_allowance, weights.relative_gap_basis),
            })

    reconciliation = _reconcile(benchmark.allowances, weights, benchmark.cohort_gap_mtco2e)
    return {
        "metadata": {"weight_version": weights.version, "budget_mtco2e": weights.budget_mtco2e,
                     "period_start": PERIOD[0], "period_end": PERIOD[1], "pathway": pathway,
                     "scenathon_id": event, "snapshot_id": snapshot, "trade_adj": trade_adj,
                     "selected_cohort_emissions_mtco2e": cohort_emissions,
                     "required_reduction_mtco2e": cohort_gap,
                     "relative_gap_basis": weights.relative_gap_basis,
                     "allowance_basis": BENCHMARK_BASIS,
                     "benchmark_version": benchmark.version,
                     "benchmark_pathway": BENCHMARK_PATHWAY,
                     "benchmark_trade_adj": False,
                     "benchmark_snapshot_id": benchmark.snapshot_id,
                     "benchmark_source": benchmark.source,
                     "benchmark_cohort_emissions_mtco2e": benchmark.cohort_emissions_mtco2e,
                     "benchmark_required_reduction_mtco2e": benchmark.cohort_gap_mtco2e},
        "summary": summary,
        "detail": detail,
        "reconciliation": reconciliation,
    }


# Frozen FABLE release payload. Use load_benchmark(), not per-selection reconstruction.
_BUNDLED_BENCHMARK_JSON = r"""
{
  "schema_version": 1,
  "status": "released",
  "benchmark_version": "scenathon-2026-ct-pretrade-38-v1",
  "allowance_basis": "fixed-current-trends-pretrade",
  "pathway": "Current Trends",
  "trade_adj": false,
  "scenathon_id": "9",
  "snapshot_id": "scenathon-9-initial-current-trends-before-trade-corrected-38-v1",
  "source": "Current Trends | before trade adjustment | Scenathon 2026-27 | 38 corrected initial Current Trends submissions | before trade",
  "source_audit_sha256": "4401fe2ded19e9ea813eb9a9cba10105d928246948a58598b43ccf7ffbad2b59",
  "weight_version": "scenathon-2026-fs-38-v1",
  "budget_mtco2e": 40000.0,
  "unit": "MtCO2e",
  "period_start": 2026,
  "period_end": 2050,
  "cohort_emissions_mtco2e": 298934.92424767686,
  "cohort_gap_mtco2e": 258934.92424767686,
  "reference_afolu_mtco2e": {
    "ARG": 15295.031501315545,
    "BRA": 20772.47631903481,
    "CAN": 3153.6855131454076,
    "CHL": 27.7195805650127,
    "CHN": 20713.576303141108,
    "CZE": 89.89379290568438,
    "DEN": 75.90451924091987,
    "DOM": 378.82378768928095,
    "ETH": 6223.28540592578,
    "FIN": 149.50304239109843,
    "GRE": 198.73923020327734,
    "GUA": 156.50324263388296,
    "IND": 21938.570379207176,
    "IRE": 368.9848175854967,
    "MEX": 2083.74564743985,
    "NEP": 574.6187826746534,
    "NOR": 76.17745972657647,
    "PAK": 9190.609013731902,
    "POL": 669.0621363464559,
    "POR": 20.58136099726277,
    "RUS": 2016.315444618173,
    "R_ASE": 24760.64641329272,
    "R_ASP": -3492.2758313527775,
    "R_CAS": 1884.1587829183425,
    "R_CSA": 20898.859445678252,
    "R_EAF": 79839.41180943265,
    "R_EEU": 6459.579498855627,
    "R_MEA": 10513.07486507498,
    "R_NEU": 1932.7611950368803,
    "R_SAF": 8567.698109388486,
    "R_SIS": 1338.2868102980815,
    "R_WAF": 21719.14831842614,
    "SAF": 2249.0858312917903,
    "SWE": 127.36157183884247,
    "TUR": 1782.4585902762633,
    "UK": 2361.835734505096,
    "USA": 12741.052774827476,
    "UZB": 1077.9730473686295
  },
  "allowances": [
    {
      "participant_id": "ARG",
      "approach": "AP",
      "base_allowance_mtco2e": 14809.787118085284,
      "adjusted_allowance_mtco2e": 14929.487513120237
    },
    {
      "participant_id": "ARG",
      "approach": "HR",
      "base_allowance_mtco2e": 12664.684378869537,
      "adjusted_allowance_mtco2e": 12736.185769653255
    },
    {
      "participant_id": "ARG",
      "approach": "EQ",
      "base_allowance_mtco2e": 13693.861948700469,
      "adjusted_allowance_mtco2e": 13759.337547428087
    },
    {
      "participant_id": "BRA",
      "approach": "AP",
      "base_allowance_mtco2e": 8567.635301754028,
      "adjusted_allowance_mtco2e": 8659.838333562386
    },
    {
      "participant_id": "BRA",
      "approach": "HR",
      "base_allowance_mtco2e": 7850.452411143551,
      "adjusted_allowance_mtco2e": 7905.528628785506
    },
    {
      "participant_id": "BRA",
      "approach": "EQ",
      "base_allowance_mtco2e": 13495.574875575869,
      "adjusted_allowance_mtco2e": 13546.00953546476
    },
    {
      "participant_id": "CAN",
      "approach": "AP",
      "base_allowance_mtco2e": -16240.35534314172,
      "adjusted_allowance_mtco2e": -16176.263612934721
    },
    {
      "participant_id": "CAN",
      "approach": "HR",
      "base_allowance_mtco2e": -2429.923245534188,
      "adjusted_allowance_mtco2e": -2391.638928709693
    },
    {
      "participant_id": "CAN",
      "approach": "EQ",
      "base_allowance_mtco2e": 1793.3644325030136,
      "adjusted_allowance_mtco2e": 1828.4223319156836
    },
    {
      "participant_id": "CHL",
      "approach": "AP",
      "base_allowance_mtco2e": -160.17371401389545,
      "adjusted_allowance_mtco2e": 129.68807897663493
    },
    {
      "participant_id": "CHL",
      "approach": "HR",
      "base_allowance_mtco2e": -464.8448552355148,
      "adjusted_allowance_mtco2e": -236.57187869046322
    },
    {
      "participant_id": "CHL",
      "approach": "EQ",
      "base_allowance_mtco2e": -633.7426785378105,
      "adjusted_allowance_mtco2e": -401.92569835951593
    },
    {
      "participant_id": "CHN",
      "approach": "AP",
      "base_allowance_mtco2e": -17376.610643279066,
      "adjusted_allowance_mtco2e": -17808.533088345695
    },
    {
      "participant_id": "CHN",
      "approach": "HR",
      "base_allowance_mtco2e": -18115.742016809778,
      "adjusted_allowance_mtco2e": -18436.50287298522
    },
    {
      "participant_id": "CHN",
      "approach": "EQ",
      "base_allowance_mtco2e": -32897.6655505239,
      "adjusted_allowance_mtco2e": -33217.329046476785
    },
    {
      "participant_id": "CZE",
      "approach": "AP",
      "base_allowance_mtco2e": 79.93697377290258,
      "adjusted_allowance_mtco2e": 67.32450802332184
    },
    {
      "participant_id": "CZE",
      "approach": "HR",
      "base_allowance_mtco2e": -866.9590728820914,
      "adjusted_allowance_mtco2e": -874.4929564757396
    },
    {
      "participant_id": "CZE",
      "approach": "EQ",
      "base_allowance_mtco2e": -403.8198357144648,
      "adjusted_allowance_mtco2e": -410.7187999672079
    },
    {
      "participant_id": "DEN",
      "approach": "AP",
      "base_allowance_mtco2e": 73.3568646588894,
      "adjusted_allowance_mtco2e": 59.980130019701846
    },
    {
      "participant_id": "DEN",
      "approach": "HR",
      "base_allowance_mtco2e": -344.12225410826704,
      "adjusted_allowance_mtco2e": -352.1126632507545
    },
    {
      "participant_id": "DEN",
      "approach": "EQ",
      "base_allowance_mtco2e": -177.13925715554961,
      "adjusted_allowance_mtco2e": -184.45627318766472
    },
    {
      "participant_id": "DOM",
      "approach": "AP",
      "base_allowance_mtco2e": 377.2099807827353,
      "adjusted_allowance_mtco2e": 587.3319290916647
    },
    {
      "participant_id": "DOM",
      "approach": "HR",
      "base_allowance_mtco2e": 134.74279195121233,
      "adjusted_allowance_mtco2e": 314.22577866490064
    },
    {
      "participant_id": "DOM",
      "approach": "EQ",
      "base_allowance_mtco2e": 25.236736349658543,
      "adjusted_allowance_mtco2e": 211.8967649835494
    },
    {
      "participant_id": "ETH",
      "approach": "AP",
      "base_allowance_mtco2e": 6153.131258934741,
      "adjusted_allowance_mtco2e": 6097.835995079667
    },
    {
      "participant_id": "ETH",
      "approach": "HR",
      "base_allowance_mtco2e": 4922.364660272997,
      "adjusted_allowance_mtco2e": 4863.9123279395135
    },
    {
      "participant_id": "ETH",
      "approach": "EQ",
      "base_allowance_mtco2e": 3385.703809709208,
      "adjusted_allowance_mtco2e": 3321.6716785639405
    },
    {
      "participant_id": "FIN",
      "approach": "AP",
      "base_allowance_mtco2e": -20.472090220309838,
      "adjusted_allowance_mtco2e": -329.48439164651955
    },
    {
      "participant_id": "FIN",
      "approach": "HR",
      "base_allowance_mtco2e": -201.4252265652689,
      "adjusted_allowance_mtco2e": -447.83899583448874
    },
    {
      "participant_id": "FIN",
      "approach": "EQ",
      "base_allowance_mtco2e": -92.69931918110248,
      "adjusted_allowance_mtco2e": -343.89768017263816
    },
    {
      "participant_id": "GRE",
      "approach": "AP",
      "base_allowance_mtco2e": 179.08385174772582,
      "adjusted_allowance_mtco2e": 199.62470551238422
    },
    {
      "participant_id": "GRE",
      "approach": "HR",
      "base_allowance_mtco2e": -271.1713119103364,
      "adjusted_allowance_mtco2e": -268.4613014346039
    },
    {
      "participant_id": "GRE",
      "approach": "EQ",
      "base_allowance_mtco2e": -285.8144463398536,
      "adjusted_allowance_mtco2e": -287.283421728879
    },
    {
      "participant_id": "GUA",
      "approach": "AP",
      "base_allowance_mtco2e": 150.7848776382824,
      "adjusted_allowance_mtco2e": 174.17909723556517
    },
    {
      "participant_id": "GUA",
      "approach": "HR",
      "base_allowance_mtco2e": -226.36921415087204,
      "adjusted_allowance_mtco2e": -212.39499763289817
    },
    {
      "participant_id": "GUA",
      "approach": "EQ",
      "base_allowance_mtco2e": -323.7220208185451,
      "adjusted_allowance_mtco2e": -310.92548381087545
    },
    {
      "participant_id": "IND",
      "approach": "AP",
      "base_allowance_mtco2e": 19571.057836957814,
      "adjusted_allowance_mtco2e": 19384.261661926306
    },
    {
      "participant_id": "IND",
      "approach": "HR",
      "base_allowance_mtco2e": 5737.361327542041,
      "adjusted_allowance_mtco2e": 5560.5973506746295
    },
    {
      "participant_id": "IND",
      "approach": "EQ",
      "base_allowance_mtco2e": -22217.72632496025,
      "adjusted_allowance_mtco2e": -22406.53082725699
    },
    {
      "participant_id": "IRE",
      "approach": "AP",
      "base_allowance_mtco2e": 363.5054231612671,
      "adjusted_allowance_mtco2e": 544.3988696625455
    },
    {
      "participant_id": "IRE",
      "approach": "HR",
      "base_allowance_mtco2e": -68.24753213963567,
      "adjusted_allowance_mtco2e": 89.83391753274444
    },
    {
      "participant_id": "IRE",
      "approach": "EQ",
      "base_allowance_mtco2e": 188.13206924892268,
      "adjusted_allowance_mtco2e": 353.5650197888534
    },
    {
      "participant_id": "MEX",
      "approach": "AP",
      "base_allowance_mtco2e": 785.3455714936679,
      "adjusted_allowance_mtco2e": 805.1484171083913
    },
    {
      "participant_id": "MEX",
      "approach": "HR",
      "base_allowance_mtco2e": -2700.218288226292,
      "adjusted_allowance_mtco2e": -2670.336771767462
    },
    {
      "participant_id": "MEX",
      "approach": "EQ",
      "base_allowance_mtco2e": -1973.9577528527366,
      "adjusted_allowance_mtco2e": -1939.1342541732233
    },
    {
      "participant_id": "NEP",
      "approach": "AP",
      "base_allowance_mtco2e": 570.5767795577158,
      "adjusted_allowance_mtco2e": 698.1755291840689
    },
    {
      "participant_id": "NEP",
      "approach": "HR",
      "base_allowance_mtco2e": 294.316421185815,
      "adjusted_allowance_mtco2e": 369.6697998077567
    },
    {
      "participant_id": "NEP",
      "approach": "EQ",
      "base_allowance_mtco2e": -405.7959328847594,
      "adjusted_allowance_mtco2e": -337.15084355800445
    },
    {
      "participant_id": "NOR",
      "approach": "AP",
      "base_allowance_mtco2e": -143.31537806174626,
      "adjusted_allowance_mtco2e": -124.29858416894741
    },
    {
      "participant_id": "NOR",
      "approach": "HR",
      "base_allowance_mtco2e": -292.2627007654902,
      "adjusted_allowance_mtco2e": -255.34443112017573
    },
    {
      "participant_id": "NOR",
      "approach": "EQ",
      "base_allowance_mtco2e": -135.72835831049835,
      "adjusted_allowance_mtco2e": -91.35914196840014
    },
    {
      "participant_id": "PAK",
      "approach": "AP",
      "base_allowance_mtco2e": 9091.043149725048,
      "adjusted_allowance_mtco2e": 8959.603728962202
    },
    {
      "participant_id": "PAK",
      "approach": "HR",
      "base_allowance_mtco2e": 6854.539028014997,
      "adjusted_allowance_mtco2e": 6724.3514430284895
    },
    {
      "participant_id": "PAK",
      "approach": "EQ",
      "base_allowance_mtco2e": 3017.1307114537713,
      "adjusted_allowance_mtco2e": 2876.560314019638
    },
    {
      "participant_id": "POL",
      "approach": "AP",
      "base_allowance_mtco2e": 589.2427727086041,
      "adjusted_allowance_mtco2e": 700.0990508077965
    },
    {
      "participant_id": "POL",
      "approach": "HR",
      "base_allowance_mtco2e": -1926.6924705911294,
      "adjusted_allowance_mtco2e": -1860.4739918727896
    },
    {
      "participant_id": "POL",
      "approach": "EQ",
      "base_allowance_mtco2e": -1081.6887052852326,
      "adjusted_allowance_mtco2e": -1021.0508002963609
    },
    {
      "participant_id": "POR",
      "approach": "AP",
      "base_allowance_mtco2e": 12.69099829055714,
      "adjusted_allowance_mtco2e": 393.18224361477144
    },
    {
      "participant_id": "POR",
      "approach": "HR",
      "base_allowance_mtco2e": -287.6590616347162,
      "adjusted_allowance_mtco2e": 43.3109300653457
    },
    {
      "participant_id": "POR",
      "approach": "EQ",
      "base_allowance_mtco2e": -454.5212162111551,
      "adjusted_allowance_mtco2e": -108.59421043726593
    },
    {
      "participant_id": "RUS",
      "approach": "AP",
      "base_allowance_mtco2e": -49525.12210461343,
      "adjusted_allowance_mtco2e": -49540.210965568396
    },
    {
      "participant_id": "RUS",
      "approach": "HR",
      "base_allowance_mtco2e": -9519.3687569874,
      "adjusted_allowance_mtco2e": -9528.381881324269
    },
    {
      "participant_id": "RUS",
      "approach": "EQ",
      "base_allowance_mtco2e": -4752.172037001235,
      "adjusted_allowance_mtco2e": -4760.425578728109
    },
    {
      "participant_id": "R_ASE",
      "approach": "AP",
      "base_allowance_mtco2e": 21670.202316556177,
      "adjusted_allowance_mtco2e": 21794.4589899962
    },
    {
      "participant_id": "R_ASE",
      "approach": "HR",
      "base_allowance_mtco2e": 6267.724503423771,
      "adjusted_allowance_mtco2e": 6341.947524680517
    },
    {
      "participant_id": "R_ASE",
      "approach": "EQ",
      "base_allowance_mtco2e": -3974.3433893922556,
      "adjusted_allowance_mtco2e": -3906.3755260697326
    },
    {
      "participant_id": "R_ASP",
      "approach": "AP",
      "base_allowance_mtco2e": -9248.310292587266,
      "adjusted_allowance_mtco2e": -9313.621526442865
    },
    {
      "participant_id": "R_ASP",
      "approach": "HR",
      "base_allowance_mtco2e": -17052.353084850096,
      "adjusted_allowance_mtco2e": -17058.740800310785
    },
    {
      "participant_id": "R_ASP",
      "approach": "EQ",
      "base_allowance_mtco2e": -12274.914700516938,
      "adjusted_allowance_mtco2e": -12267.281727201813
    },
    {
      "participant_id": "R_CAS",
      "approach": "AP",
      "base_allowance_mtco2e": 1800.0913853830807,
      "adjusted_allowance_mtco2e": 1736.7285578187732
    },
    {
      "participant_id": "R_CAS",
      "approach": "HR",
      "base_allowance_mtco2e": -1546.7363781103443,
      "adjusted_allowance_mtco2e": -1584.5852949289895
    },
    {
      "participant_id": "R_CAS",
      "approach": "EQ",
      "base_allowance_mtco2e": 395.8398213438538,
      "adjusted_allowance_mtco2e": 361.18062852635
    },
    {
      "participant_id": "R_CSA",
      "approach": "AP",
      "base_allowance_mtco2e": 19982.923484290393,
      "adjusted_allowance_mtco2e": 19763.905811270277
    },
    {
      "participant_id": "R_CSA",
      "approach": "HR",
      "base_allowance_mtco2e": 11619.488140594109,
      "adjusted_allowance_mtco2e": 11424.597856029395
    },
    {
      "participant_id": "R_CSA",
      "approach": "EQ",
      "base_allowance_mtco2e": 14963.313173507126,
      "adjusted_allowance_mtco2e": 14758.373118397745
    },
    {
      "participant_id": "R_EAF",
      "approach": "AP",
      "base_allowance_mtco2e": 79724.29394493664,
      "adjusted_allowance_mtco2e": 79621.82853174339
    },
    {
      "participant_id": "R_EAF",
      "approach": "HR",
      "base_allowance_mtco2e": 74959.583308262,
      "adjusted_allowance_mtco2e": 74868.58097603288
    },
    {
      "participant_id": "R_EAF",
      "approach": "EQ",
      "base_allowance_mtco2e": 71171.41999492793,
      "adjusted_allowance_mtco2e": 71075.7736402008
    },
    {
      "participant_id": "R_EEU",
      "approach": "AP",
      "base_allowance_mtco2e": 4449.986062946437,
      "adjusted_allowance_mtco2e": 4282.58208511464
    },
    {
      "participant_id": "R_EEU",
      "approach": "HR",
      "base_allowance_mtco2e": -12509.851453396237,
      "adjusted_allowance_mtco2e": -12641.747027437576
    },
    {
      "participant_id": "R_EEU",
      "approach": "EQ",
      "base_allowance_mtco2e": -9337.937476621264,
      "adjusted_allowance_mtco2e": -9471.89988977034
    },
    {
      "participant_id": "R_MEA",
      "approach": "AP",
      "base_allowance_mtco2e": 9898.045652862147,
      "adjusted_allowance_mtco2e": 10103.156980255395
    },
    {
      "participant_id": "R_MEA",
      "approach": "HR",
      "base_allowance_mtco2e": -7571.294775986711,
      "adjusted_allowance_mtco2e": -7351.311793258672
    },
    {
      "participant_id": "R_MEA",
      "approach": "EQ",
      "base_allowance_mtco2e": -3496.7833857357236,
      "adjusted_allowance_mtco2e": -3255.062935471655
    },
    {
      "participant_id": "R_NEU",
      "approach": "AP",
      "base_allowance_mtco2e": 1857.4194491721944,
      "adjusted_allowance_mtco2e": 1849.7018318351968
    },
    {
      "participant_id": "R_NEU",
      "approach": "HR",
      "base_allowance_mtco2e": -2878.2519943559237,
      "adjusted_allowance_mtco2e": -2882.8620073218017
    },
    {
      "participant_id": "R_NEU",
      "approach": "EQ",
      "base_allowance_mtco2e": -2801.381659394816,
      "adjusted_allowance_mtco2e": -2805.6031627218767
    },
    {
      "participant_id": "R_SAF",
      "approach": "AP",
      "base_allowance_mtco2e": 8446.196813725564,
      "adjusted_allowance_mtco2e": 8360.02628523251
    },
    {
      "participant_id": "R_SAF",
      "approach": "HR",
      "base_allowance_mtco2e": 4052.710333427961,
      "adjusted_allowance_mtco2e": 3987.58519680043
    },
    {
      "participant_id": "R_SAF",
      "approach": "EQ",
      "base_allowance_mtco2e": 5409.745960046403,
      "adjusted_allowance_mtco2e": 5344.467396365263
    },
    {
      "participant_id": "R_SIS",
      "approach": "AP",
      "base_allowance_mtco2e": 1330.3995790327954,
      "adjusted_allowance_mtco2e": 1411.8014354671523
    },
    {
      "participant_id": "R_SIS",
      "approach": "HR",
      "base_allowance_mtco2e": 302.9151109099239,
      "adjusted_allowance_mtco2e": 384.9255441280372
    },
    {
      "participant_id": "R_SIS",
      "approach": "EQ",
      "base_allowance_mtco2e": -282.07220586572976,
      "adjusted_allowance_mtco2e": -193.1763294307667
    },
    {
      "participant_id": "R_WAF",
      "approach": "AP",
      "base_allowance_mtco2e": 21484.18641204612,
      "adjusted_allowance_mtco2e": 21345.17268850105
    },
    {
      "participant_id": "R_WAF",
      "approach": "HR",
      "base_allowance_mtco2e": 15048.8643563139,
      "adjusted_allowance_mtco2e": 14934.035712005203
    },
    {
      "participant_id": "R_WAF",
      "approach": "EQ",
      "base_allowance_mtco2e": 11467.339747224058,
      "adjusted_allowance_mtco2e": 11349.050741997808
    },
    {
      "participant_id": "SAF",
      "approach": "AP",
      "base_allowance_mtco2e": 2149.1668877014035,
      "adjusted_allowance_mtco2e": 2099.8262223926895
    },
    {
      "participant_id": "SAF",
      "approach": "HR",
      "base_allowance_mtco2e": -1477.1487954776298,
      "adjusted_allowance_mtco2e": -1549.2826098455935
    },
    {
      "participant_id": "SAF",
      "approach": "EQ",
      "base_allowance_mtco2e": 369.64831255975787,
      "adjusted_allowance_mtco2e": 285.96391702850576
    },
    {
      "participant_id": "SWE",
      "approach": "AP",
      "base_allowance_mtco2e": -347.6902687318765,
      "adjusted_allowance_mtco2e": -471.9388750145735
    },
    {
      "participant_id": "SWE",
      "approach": "HR",
      "base_allowance_mtco2e": -369.6703088529759,
      "adjusted_allowance_mtco2e": -475.21940528801
    },
    {
      "participant_id": "SWE",
      "approach": "EQ",
      "base_allowance_mtco2e": -294.50501367068176,
      "adjusted_allowance_mtco2e": -404.10649028642234
    },
    {
      "participant_id": "TUR",
      "approach": "AP",
      "base_allowance_mtco2e": 1491.1723138448494,
      "adjusted_allowance_mtco2e": 1526.3309009418856
    },
    {
      "participant_id": "TUR",
      "approach": "HR",
      "base_allowance_mtco2e": -369.6438637840779,
      "adjusted_allowance_mtco2e": -330.23168132760725
    },
    {
      "participant_id": "TUR",
      "approach": "EQ",
      "base_allowance_mtco2e": -919.6099102461894,
      "adjusted_allowance_mtco2e": -875.9109486052515
    },
    {
      "participant_id": "UK",
      "approach": "AP",
      "base_allowance_mtco2e": 2094.2838746245147,
      "adjusted_allowance_mtco2e": 2134.3963372323815
    },
    {
      "participant_id": "UK",
      "approach": "HR",
      "base_allowance_mtco2e": -2038.8744533502063,
      "adjusted_allowance_mtco2e": -1980.772647592833
    },
    {
      "participant_id": "UK",
      "approach": "EQ",
      "base_allowance_mtco2e": -476.4191128055463,
      "adjusted_allowance_mtco2e": -409.10494419308424
    },
    {
      "participant_id": "USA",
      "approach": "AP",
      "base_allowance_mtco2e": -105758.98500149105,
      "adjusted_allowance_mtco2e": -105705.8997202754
    },
    {
      "participant_id": "USA",
      "approach": "HR",
      "base_allowance_mtco2e": -27020.959591627427,
      "adjusted_allowance_mtco2e": -26989.249826292435
    },
    {
      "participant_id": "USA",
      "approach": "EQ",
      "base_allowance_mtco2e": 258.75639203978426,
      "adjusted_allowance_mtco2e": 287.79381142510846
    },
    {
      "participant_id": "UZB",
      "approach": "AP",
      "base_allowance_mtco2e": 1068.2778997487515,
      "adjusted_allowance_mtco2e": 1050.1743147079003
    },
    {
      "participant_id": "UZB",
      "approach": "HR",
      "base_allowance_mtco2e": -159.9560645792078,
      "adjusted_allowance_mtco2e": -170.73399112575717
    },
    {
      "participant_id": "UZB",
      "approach": "EQ",
      "base_allowance_mtco2e": 59.09230483640545,
      "adjusted_allowance_mtco2e": 49.237567766754125
    }
  ]
}
"""
