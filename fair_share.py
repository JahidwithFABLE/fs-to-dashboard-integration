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
    """Validate the release with exactly 37 participant IDs and 111 weight pairs."""
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
        raise ValueError("Benchmark allowances must have all 111 participant–approach pairs.")
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
    """Return metadata, 37 participant summaries and 111 full detail rows.

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
  "benchmark_version": "scenathon-2026-ct-pretrade-37-v1",
  "allowance_basis": "fixed-current-trends-pretrade",
  "pathway": "Current Trends",
  "trade_adj": false,
  "scenathon_id": "9",
  "snapshot_id": "scenathon-9-initial-current-trends-before-trade-corrected-v1",
  "source": "FABLE's corrected initial Current Trends submissions before trade adjustment. Full-precision calibration audit used for the released weights.",
  "source_audit_sha256": "8b702fc099a2f035aefa1a236931378a0d2474ca3b66397ac0d30e9eee436861",
  "weight_version": "scenathon-2026-fs-37-v1",
  "budget_mtco2e": 40000.0,
  "unit": "MtCO2e",
  "period_start": 2026,
  "period_end": 2050,
  "cohort_emissions_mtco2e": 297050.7654647585,
  "cohort_gap_mtco2e": 257050.7654647585,
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
      "base_allowance_mtco2e": 14813.161587247516,
      "adjusted_allowance_mtco2e": 14933.435910967817
    },
    {
      "participant_id": "ARG",
      "approach": "HR",
      "base_allowance_mtco2e": 12648.76113258693,
      "adjusted_allowance_mtco2e": 12720.873574219147
    },
    {
      "participant_id": "ARG",
      "approach": "EQ",
      "base_allowance_mtco2e": 13696.323844319344,
      "adjusted_allowance_mtco2e": 13762.441590261542
    },
    {
      "participant_id": "BRA",
      "approach": "AP",
      "base_allowance_mtco2e": 8652.509771332843,
      "adjusted_allowance_mtco2e": 8744.753872009123
    },
    {
      "participant_id": "BRA",
      "approach": "HR",
      "base_allowance_mtco2e": 7772.226777956497,
      "adjusted_allowance_mtco2e": 7827.533240052296
    },
    {
      "participant_id": "BRA",
      "approach": "EQ",
      "base_allowance_mtco2e": 13506.763554347199,
      "adjusted_allowance_mtco2e": 13557.472399132424
    },
    {
      "participant_id": "CAN",
      "approach": "AP",
      "base_allowance_mtco2e": -16105.485996762149,
      "adjusted_allowance_mtco2e": -16041.897954757767
    },
    {
      "participant_id": "CAN",
      "approach": "HR",
      "base_allowance_mtco2e": -2463.7245551223677,
      "adjusted_allowance_mtco2e": -2425.5993025687844
    },
    {
      "participant_id": "CAN",
      "approach": "EQ",
      "base_allowance_mtco2e": 1795.4560089405174,
      "adjusted_allowance_mtco2e": 1830.411915373087
    },
    {
      "participant_id": "CHL",
      "approach": "AP",
      "base_allowance_mtco2e": -158.86707317423017,
      "adjusted_allowance_mtco2e": 130.67707590394454
    },
    {
      "participant_id": "CHL",
      "approach": "HR",
      "base_allowance_mtco2e": -467.82667643864613,
      "adjusted_allowance_mtco2e": -239.05674504463303
    },
    {
      "participant_id": "CHL",
      "approach": "EQ",
      "base_allowance_mtco2e": -632.7256400642287,
      "adjusted_allowance_mtco2e": -400.877999018579
    },
    {
      "participant_id": "CHN",
      "approach": "AP",
      "base_allowance_mtco2e": -17111.725225381542,
      "adjusted_allowance_mtco2e": -17548.994570379455
    },
    {
      "participant_id": "CHN",
      "approach": "HR",
      "base_allowance_mtco2e": -18350.801790220732,
      "adjusted_allowance_mtco2e": -18675.778351773486
    },
    {
      "participant_id": "CHN",
      "approach": "EQ",
      "base_allowance_mtco2e": -32815.23500391097,
      "adjusted_allowance_mtco2e": -33138.34969730911
    },
    {
      "participant_id": "CZE",
      "approach": "AP",
      "base_allowance_mtco2e": 80.00621512896699,
      "adjusted_allowance_mtco2e": 65.4036428785331
    },
    {
      "participant_id": "CZE",
      "approach": "HR",
      "base_allowance_mtco2e": -872.7515417574497,
      "adjusted_allowance_mtco2e": -881.5067532316665
    },
    {
      "participant_id": "CZE",
      "approach": "EQ",
      "base_allowance_mtco2e": -403.0607209678133,
      "adjusted_allowance_mtco2e": -411.0881131184606
    },
    {
      "participant_id": "DEN",
      "approach": "AP",
      "base_allowance_mtco2e": 73.37458146740939,
      "adjusted_allowance_mtco2e": 57.9929298825408
    },
    {
      "participant_id": "DEN",
      "approach": "HR",
      "base_allowance_mtco2e": -346.66495643840983,
      "adjusted_allowance_mtco2e": -355.8872776937795
    },
    {
      "participant_id": "DEN",
      "approach": "EQ",
      "base_allowance_mtco2e": -176.75018695153727,
      "adjusted_allowance_mtco2e": -185.20585812225838
    },
    {
      "participant_id": "DOM",
      "approach": "AP",
      "base_allowance_mtco2e": 377.2212034610972,
      "adjusted_allowance_mtco2e": 585.568293879842
    },
    {
      "participant_id": "DOM",
      "approach": "HR",
      "base_allowance_mtco2e": 133.26520681620286,
      "adjusted_allowance_mtco2e": 312.1927636338115
    },
    {
      "participant_id": "DOM",
      "approach": "EQ",
      "base_allowance_mtco2e": 25.78039795698959,
      "adjusted_allowance_mtco2e": 211.46473147278203
    },
    {
      "participant_id": "ETH",
      "approach": "AP",
      "base_allowance_mtco2e": 6153.619122398606,
      "adjusted_allowance_mtco2e": 6097.438418456708
    },
    {
      "participant_id": "ETH",
      "approach": "HR",
      "base_allowance_mtco2e": 4914.4893189348095,
      "adjusted_allowance_mtco2e": 4855.363892337827
    },
    {
      "participant_id": "ETH",
      "approach": "EQ",
      "base_allowance_mtco2e": 3390.066764081229,
      "adjusted_allowance_mtco2e": 3325.6670953648418
    },
    {
      "participant_id": "FIN",
      "approach": "AP",
      "base_allowance_mtco2e": -19.290055222758014,
      "adjusted_allowance_mtco2e": -331.33797956250453
    },
    {
      "participant_id": "FIN",
      "approach": "HR",
      "base_allowance_mtco2e": -203.54962954099662,
      "adjusted_allowance_mtco2e": -452.5185494196127
    },
    {
      "participant_id": "FIN",
      "approach": "EQ",
      "base_allowance_mtco2e": -92.32691831208632,
      "adjusted_allowance_mtco2e": -345.38037144442364
    },
    {
      "participant_id": "GRE",
      "approach": "AP",
      "base_allowance_mtco2e": 179.22053847870606,
      "adjusted_allowance_mtco2e": 199.14017761310066
    },
    {
      "participant_id": "GRE",
      "approach": "HR",
      "base_allowance_mtco2e": -274.01599397872826,
      "adjusted_allowance_mtco2e": -271.63974239009764
    },
    {
      "participant_id": "GRE",
      "approach": "EQ",
      "base_allowance_mtco2e": -285.06941557690686,
      "adjusted_allowance_mtco2e": -286.7222661997596
    },
    {
      "participant_id": "GUA",
      "approach": "AP",
      "base_allowance_mtco2e": 150.8246440879373,
      "adjusted_allowance_mtco2e": 172.92651557021398
    },
    {
      "participant_id": "GUA",
      "approach": "HR",
      "base_allowance_mtco2e": -228.6869966109076,
      "adjusted_allowance_mtco2e": -215.4354573968116
    },
    {
      "participant_id": "GUA",
      "approach": "EQ",
      "base_allowance_mtco2e": -322.9836452541108,
      "adjusted_allowance_mtco2e": -310.83370436639
    },
    {
      "participant_id": "IND",
      "approach": "AP",
      "base_allowance_mtco2e": 19587.521908137445,
      "adjusted_allowance_mtco2e": 19400.31337379815
    },
    {
      "participant_id": "IND",
      "approach": "HR",
      "base_allowance_mtco2e": 5639.284598579452,
      "adjusted_allowance_mtco2e": 5461.808350743883
    },
    {
      "participant_id": "IND",
      "approach": "EQ",
      "base_allowance_mtco2e": -22149.83333185141,
      "adjusted_allowance_mtco2e": -22338.68170592091
    },
    {
      "participant_id": "IRE",
      "approach": "AP",
      "base_allowance_mtco2e": 363.543527770308,
      "adjusted_allowance_mtco2e": 542.3952758485516
    },
    {
      "participant_id": "IRE",
      "approach": "HR",
      "base_allowance_mtco2e": -70.89439130433863,
      "adjusted_allowance_mtco2e": 86.40352673513226
    },
    {
      "participant_id": "IRE",
      "approach": "EQ",
      "base_allowance_mtco2e": 188.41014135444348,
      "adjusted_allowance_mtco2e": 352.6828329758282
    },
    {
      "participant_id": "MEX",
      "approach": "AP",
      "base_allowance_mtco2e": 794.3748589371496,
      "adjusted_allowance_mtco2e": 811.4439856571515
    },
    {
      "participant_id": "MEX",
      "approach": "HR",
      "base_allowance_mtco2e": -2729.17881412216,
      "adjusted_allowance_mtco2e": -2700.8787600077385
    },
    {
      "participant_id": "MEX",
      "approach": "EQ",
      "base_allowance_mtco2e": -1967.7187869621075,
      "adjusted_allowance_mtco2e": -1934.5358425124582
    },
    {
      "participant_id": "NEP",
      "approach": "AP",
      "base_allowance_mtco2e": 570.6048883114554,
      "adjusted_allowance_mtco2e": 698.9964299267557
    },
    {
      "participant_id": "NEP",
      "approach": "HR",
      "base_allowance_mtco2e": 292.61956395450306,
      "adjusted_allowance_mtco2e": 368.73218670292295
    },
    {
      "participant_id": "NEP",
      "approach": "EQ",
      "base_allowance_mtco2e": -404.2884856010065,
      "adjusted_allowance_mtco2e": -334.8501803594231
    },
    {
      "participant_id": "NOR",
      "approach": "AP",
      "base_allowance_mtco2e": -141.78898880708448,
      "adjusted_allowance_mtco2e": -126.09153323926412
    },
    {
      "participant_id": "NOR",
      "approach": "HR",
      "base_allowance_mtco2e": -294.4931149058349,
      "adjusted_allowance_mtco2e": -259.50359760358947
    },
    {
      "participant_id": "NOR",
      "approach": "EQ",
      "base_allowance_mtco2e": -135.40254022072278,
      "adjusted_allowance_mtco2e": -93.07770370243534
    },
    {
      "participant_id": "PAK",
      "approach": "AP",
      "base_allowance_mtco2e": 9091.73554710112,
      "adjusted_allowance_mtco2e": 8959.929922115987
    },
    {
      "participant_id": "PAK",
      "approach": "HR",
      "base_allowance_mtco2e": 6840.3972376031725,
      "adjusted_allowance_mtco2e": 6709.65854926791
    },
    {
      "participant_id": "PAK",
      "approach": "EQ",
      "base_allowance_mtco2e": 3026.6228100018498,
      "adjusted_allowance_mtco2e": 2886.0414377811476
    },
    {
      "participant_id": "POL",
      "approach": "AP",
      "base_allowance_mtco2e": 589.797849676939,
      "adjusted_allowance_mtco2e": 701.0566700453763
    },
    {
      "participant_id": "POL",
      "approach": "HR",
      "base_allowance_mtco2e": -1942.406305294833,
      "adjusted_allowance_mtco2e": -1875.6992564275602
    },
    {
      "participant_id": "POL",
      "approach": "EQ",
      "base_allowance_mtco2e": -1078.9968193324194,
      "adjusted_allowance_mtco2e": -1017.8351167228441
    },
    {
      "participant_id": "POR",
      "approach": "AP",
      "base_allowance_mtco2e": 12.745869169134574,
      "adjusted_allowance_mtco2e": 390.9857133224522
    },
    {
      "participant_id": "POR",
      "approach": "HR",
      "base_allowance_mtco2e": -289.5250465923439,
      "adjusted_allowance_mtco2e": 41.02069922470804
    },
    {
      "participant_id": "POR",
      "approach": "EQ",
      "base_allowance_mtco2e": -453.7907170885382,
      "adjusted_allowance_mtco2e": -109.16470776717375
    },
    {
      "participant_id": "RUS",
      "approach": "AP",
      "base_allowance_mtco2e": -49166.69448010553,
      "adjusted_allowance_mtco2e": -49183.821436580736
    },
    {
      "participant_id": "RUS",
      "approach": "HR",
      "base_allowance_mtco2e": -9589.201950870089,
      "adjusted_allowance_mtco2e": -9599.470698271472
    },
    {
      "participant_id": "RUS",
      "approach": "EQ",
      "base_allowance_mtco2e": -4741.765075633402,
      "adjusted_allowance_mtco2e": -4751.180183712699
    },
    {
      "participant_id": "R_ASE",
      "approach": "AP",
      "base_allowance_mtco2e": 21691.693772536808,
      "adjusted_allowance_mtco2e": 21816.61266886185
    },
    {
      "participant_id": "R_ASE",
      "approach": "HR",
      "base_allowance_mtco2e": 6155.774507340357,
      "adjusted_allowance_mtco2e": 6230.671678584896
    },
    {
      "participant_id": "R_ASE",
      "approach": "EQ",
      "base_allowance_mtco2e": -3930.161594036388,
      "adjusted_allowance_mtco2e": -3861.4906126072524
    },
    {
      "participant_id": "R_ASP",
      "approach": "AP",
      "base_allowance_mtco2e": -9208.281883279604,
      "adjusted_allowance_mtco2e": -9279.083558638262
    },
    {
      "participant_id": "R_ASP",
      "approach": "HR",
      "base_allowance_mtco2e": -17134.441280691648,
      "adjusted_allowance_mtco2e": -17144.24227446195
    },
    {
      "participant_id": "R_ASP",
      "approach": "EQ",
      "base_allowance_mtco2e": -12261.410858828933,
      "adjusted_allowance_mtco2e": -12257.320968134212
    },
    {
      "participant_id": "R_CSA",
      "approach": "AP",
      "base_allowance_mtco2e": 19989.293053445854,
      "adjusted_allowance_mtco2e": 19769.153449942678
    },
    {
      "participant_id": "R_CSA",
      "approach": "HR",
      "base_allowance_mtco2e": 11563.31391424287,
      "adjusted_allowance_mtco2e": 11367.214900137387
    },
    {
      "participant_id": "R_CSA",
      "approach": "EQ",
      "base_allowance_mtco2e": 14972.43943708067,
      "adjusted_allowance_mtco2e": 14766.965606216492
    },
    {
      "participant_id": "R_EAF",
      "approach": "AP",
      "base_allowance_mtco2e": 79725.09449347916,
      "adjusted_allowance_mtco2e": 79621.1618869789
    },
    {
      "participant_id": "R_EAF",
      "approach": "HR",
      "base_allowance_mtco2e": 74930.04245018362,
      "adjusted_allowance_mtco2e": 74837.90977879406
    },
    {
      "participant_id": "R_EAF",
      "approach": "EQ",
      "base_allowance_mtco2e": 71184.74755979309,
      "adjusted_allowance_mtco2e": 71088.33161040043
    },
    {
      "participant_id": "R_EEU",
      "approach": "AP",
      "base_allowance_mtco2e": 4463.961105857718,
      "adjusted_allowance_mtco2e": 4293.991326162736
    },
    {
      "participant_id": "R_EEU",
      "approach": "HR",
      "base_allowance_mtco2e": -12624.686076673155,
      "adjusted_allowance_mtco2e": -12758.517060971504
    },
    {
      "participant_id": "R_EEU",
      "approach": "EQ",
      "base_allowance_mtco2e": -9313.647832987,
      "adjusted_allowance_mtco2e": -9449.138636187397
    },
    {
      "participant_id": "R_MEA",
      "approach": "AP",
      "base_allowance_mtco2e": 9902.322667047596,
      "adjusted_allowance_mtco2e": 10102.257096652915
    },
    {
      "participant_id": "R_MEA",
      "approach": "HR",
      "base_allowance_mtco2e": -7680.771532572564,
      "adjusted_allowance_mtco2e": -7463.36256693704
    },
    {
      "participant_id": "R_MEA",
      "approach": "EQ",
      "base_allowance_mtco2e": -3475.242376170303,
      "adjusted_allowance_mtco2e": -3236.8435742909146
    },
    {
      "participant_id": "R_NEU",
      "approach": "AP",
      "base_allowance_mtco2e": 1857.9433880509025,
      "adjusted_allowance_mtco2e": 1848.3305194182567
    },
    {
      "participant_id": "R_NEU",
      "approach": "HR",
      "base_allowance_mtco2e": -2907.376267433742,
      "adjusted_allowance_mtco2e": -2913.1398203413596
    },
    {
      "participant_id": "R_NEU",
      "approach": "EQ",
      "base_allowance_mtco2e": -2794.10262668187,
      "adjusted_allowance_mtco2e": -2799.387056362032
    },
    {
      "participant_id": "R_SAF",
      "approach": "AP",
      "base_allowance_mtco2e": 8447.041753697165,
      "adjusted_allowance_mtco2e": 8358.493080876102
    },
    {
      "participant_id": "R_SAF",
      "approach": "HR",
      "base_allowance_mtco2e": 4025.3780997063814,
      "adjusted_allowance_mtco2e": 3958.6247896581303
    },
    {
      "participant_id": "R_SAF",
      "approach": "EQ",
      "base_allowance_mtco2e": 5414.601503638516,
      "adjusted_allowance_mtco2e": 5347.925519715381
    },
    {
      "participant_id": "R_SIS",
      "approach": "AP",
      "base_allowance_mtco2e": 1330.4544281348146,
      "adjusted_allowance_mtco2e": 1409.1511002970121
    },
    {
      "participant_id": "R_SIS",
      "approach": "HR",
      "base_allowance_mtco2e": 296.64731514155847,
      "adjusted_allowance_mtco2e": 377.24216572035607
    },
    {
      "participant_id": "R_SIS",
      "approach": "EQ",
      "base_allowance_mtco2e": -279.58080527896186,
      "adjusted_allowance_mtco2e": -192.30461539991182
    },
    {
      "participant_id": "R_WAF",
      "approach": "AP",
      "base_allowance_mtco2e": 21485.8203757452,
      "adjusted_allowance_mtco2e": 21344.782776004515
    },
    {
      "participant_id": "R_WAF",
      "approach": "HR",
      "base_allowance_mtco2e": 15008.484676614109,
      "adjusted_allowance_mtco2e": 14892.108935840615
    },
    {
      "participant_id": "R_WAF",
      "approach": "EQ",
      "base_allowance_mtco2e": 11483.102526728439,
      "adjusted_allowance_mtco2e": 11363.659362347344
    },
    {
      "participant_id": "SAF",
      "approach": "AP",
      "base_allowance_mtco2e": 2149.8617404509073,
      "adjusted_allowance_mtco2e": 2101.0609341258355
    },
    {
      "participant_id": "SAF",
      "approach": "HR",
      "base_allowance_mtco2e": -1499.7061800762272,
      "adjusted_allowance_mtco2e": -1571.6580642074287
    },
    {
      "participant_id": "SAF",
      "approach": "EQ",
      "base_allowance_mtco2e": 372.5380621092422,
      "adjusted_allowance_mtco2e": 289.4690506883228
    },
    {
      "participant_id": "SWE",
      "approach": "AP",
      "base_allowance_mtco2e": -344.3866801858404,
      "adjusted_allowance_mtco2e": -470.4079754523194
    },
    {
      "participant_id": "SWE",
      "approach": "HR",
      "base_allowance_mtco2e": -372.679174481024,
      "adjusted_allowance_mtco2e": -479.591494598135
    },
    {
      "participant_id": "SWE",
      "approach": "EQ",
      "base_allowance_mtco2e": -293.8563681247308,
      "adjusted_allowance_mtco2e": -404.43851358130087
    },
    {
      "participant_id": "TUR",
      "approach": "AP",
      "base_allowance_mtco2e": 1493.1979664668459,
      "adjusted_allowance_mtco2e": 1525.8931940236273
    },
    {
      "participant_id": "TUR",
      "approach": "HR",
      "base_allowance_mtco2e": -382.6719759708599,
      "adjusted_allowance_mtco2e": -344.6446465576369
    },
    {
      "participant_id": "TUR",
      "approach": "EQ",
      "base_allowance_mtco2e": -915.4553155048175,
      "adjusted_allowance_mtco2e": -873.2101863159287
    },
    {
      "participant_id": "UK",
      "approach": "AP",
      "base_allowance_mtco2e": 2096.144474214467,
      "adjusted_allowance_mtco2e": 2132.69424996703
    },
    {
      "participant_id": "UK",
      "approach": "HR",
      "base_allowance_mtco2e": -2065.5148885770345,
      "adjusted_allowance_mtco2e": -2009.434312857029
    },
    {
      "participant_id": "UK",
      "approach": "EQ",
      "base_allowance_mtco2e": -472.05512326900043,
      "adjusted_allowance_mtco2e": -406.95272585690685
    },
    {
      "participant_id": "USA",
      "approach": "AP",
      "base_allowance_mtco2e": -104934.91627031346,
      "adjusted_allowance_mtco2e": -104882.54796663845
    },
    {
      "participant_id": "USA",
      "approach": "HR",
      "base_allowance_mtco2e": -27261.665584460177,
      "adjusted_allowance_mtco2e": -27230.26730987146
    },
    {
      "participant_id": "USA",
      "approach": "EQ",
      "base_allowance_mtco2e": 277.9486822804447,
      "adjusted_allowance_mtco2e": 306.73682334586596
    },
    {
      "participant_id": "UZB",
      "approach": "AP",
      "base_allowance_mtco2e": 1068.345321398125,
      "adjusted_allowance_mtco2e": 1048.1424840610562
    },
    {
      "participant_id": "UZB",
      "approach": "HR",
      "base_allowance_mtco2e": -167.4500755261829,
      "adjusted_allowance_mtco2e": -179.5269890202941
    },
    {
      "participant_id": "UZB",
      "approach": "EQ",
      "base_allowance_mtco2e": 60.65889597730029,
      "adjusted_allowance_mtco2e": 49.60036393729042
    }
  ]
}
"""
