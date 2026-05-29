"""Dependency-free validation for SM90 FlashMask proof artifacts."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


SPARSE_FA3_BACKEND_KIND = "sm90_sparse_fa3"
REQUIRED_FLASHMASK_CUDA_KERNEL_MARKERS = (
    "prepare_flashmask_kernel",
    "scanMaxMinChunkedKernel",
    "cutlass_flashmask_kernel",
)


class ProofValidationError(ValueError):
    """Raised when a benchmark JSONL artifact is not valid proof."""


def validate_sm90_proof_jsonl(
    paths: list[str | Path],
    *,
    min_speedup: float,
    required_cases: set[str] | None = None,
    require_speedup: bool = True,
) -> list[dict[str, Any]]:
    """Load and validate one or more SM90 proof JSONL files."""

    records: list[dict[str, Any]] = []
    for path in paths:
        records.extend(_read_jsonl(Path(path)))
    validate_sm90_proof_records(
        records,
        min_speedup=min_speedup,
        required_cases=required_cases,
        require_speedup=require_speedup,
    )
    return records


def validate_sm90_proof_records(
    records: list[dict[str, Any]],
    *,
    min_speedup: float,
    required_cases: set[str] | None = None,
    require_speedup: bool = True,
) -> None:
    """Validate parsed benchmark records as real SM90 sparse-kernel proof."""

    if not records:
        raise ProofValidationError("proof artifact contains no records")
    if min_speedup <= 0:
        raise ProofValidationError("min_speedup must be positive")

    seen_cases: set[str] = set()
    speedup_records = 0
    for index, record in enumerate(records):
        _validate_record(index, record, min_speedup=min_speedup)
        case = record.get("case")
        if isinstance(case, str):
            seen_cases.add(case)
        speedup = record.get("speedup")
        if speedup is not None:
            speedup_records += 1

    if require_speedup and speedup_records == 0:
        raise ProofValidationError("proof artifact contains no benchmark speedup records")
    if required_cases:
        missing_cases = sorted(required_cases - seen_cases)
        if missing_cases:
            raise ProofValidationError(
                "proof artifact is missing required cases: " + ", ".join(missing_cases)
            )


def _validate_record(index: int, record: dict[str, Any], *, min_speedup: float) -> None:
    prefix = f"record {index}"
    if not isinstance(record, dict):
        raise ProofValidationError(f"{prefix}: expected JSON object")
    if record.get("status") != "ok":
        raise ProofValidationError(f"{prefix}: status is not ok")
    if record.get("passed") is not True:
        raise ProofValidationError(f"{prefix}: passed is not true")
    if record.get("capability") != [9, 0]:
        raise ProofValidationError(f"{prefix}: capability is not [9, 0]")
    if record.get("backend_kind") != SPARSE_FA3_BACKEND_KIND:
        raise ProofValidationError(f"{prefix}: backend_kind is not {SPARSE_FA3_BACKEND_KIND}")
    backend = record.get("backend")
    if backend not in ("fa3", "flashmask-fa3"):
        raise ProofValidationError(f"{prefix}: backend is not fa3 or flashmask-fa3")
    if record.get("kernel_ready") is not True:
        raise ProofValidationError(f"{prefix}: kernel_ready is not true")
    if record.get("forward_ready") is not True:
        raise ProofValidationError(f"{prefix}: forward_ready is not true")
    if record.get("profiler_check_skipped") is True:
        raise ProofValidationError(f"{prefix}: profiler check was skipped")
    if record.get("profiler_flashmask_fwd") is not True:
        raise ProofValidationError(f"{prefix}: flashmask::fwd was not profiled")
    if record.get("profiler_dense_attention_events") not in ([], ()):
        raise ProofValidationError(f"{prefix}: dense attention events were profiled")
    if record.get("profiler_missing_flashmask_cuda_kernel_markers") not in ([], ()):
        raise ProofValidationError(f"{prefix}: required FlashMask CUDA kernel markers are missing")

    kernel_events = record.get("profiler_flashmask_cuda_kernel_events")
    if not isinstance(kernel_events, list):
        raise ProofValidationError(f"{prefix}: CUDA kernel event list is missing")
    for marker in REQUIRED_FLASHMASK_CUDA_KERNEL_MARKERS:
        if not any(marker in str(event) for event in kernel_events):
            raise ProofValidationError(f"{prefix}: missing CUDA kernel event marker {marker}")

    speedup = record.get("speedup")
    if speedup is not None:
        numeric_speedup = _finite_float(prefix, "speedup", speedup)
        if numeric_speedup < min_speedup:
            raise ProofValidationError(
                f"{prefix}: speedup {numeric_speedup:.4f} is below required {min_speedup:.4f}"
            )
    _validate_parity_tolerances(prefix, record)
    for metric in (
        "max_abs_error",
        "max_rel_error",
        "out_max_abs",
        "out_max_rel",
        "lse_max_abs",
        "lse_max_rel",
    ):
        if metric in record and record[metric] is not None:
            _finite_float(prefix, metric, record[metric])


def _validate_parity_tolerances(prefix: str, record: dict[str, Any]) -> None:
    if "atol" not in record or "rtol" not in record:
        raise ProofValidationError(f"{prefix}: parity tolerances are missing")
    atol = _finite_float(prefix, "atol", record["atol"])
    rtol = _finite_float(prefix, "rtol", record["rtol"])
    if atol < 0 or rtol < 0:
        raise ProofValidationError(f"{prefix}: parity tolerances must be non-negative")

    if "max_abs_error" in record or "max_rel_error" in record:
        if "max_abs_error" not in record or "max_rel_error" not in record:
            raise ProofValidationError(f"{prefix}: PE parity abs/rel metrics are incomplete")
        _validate_abs_rel_pair(
            prefix,
            "max_abs_error",
            record["max_abs_error"],
            "max_rel_error",
            record["max_rel_error"],
            atol,
            rtol,
        )

    for label in ("out", "lse"):
        abs_name = f"{label}_max_abs"
        rel_name = f"{label}_max_rel"
        if abs_name in record or rel_name in record:
            if abs_name not in record or rel_name not in record:
                raise ProofValidationError(f"{prefix}: {label} parity abs/rel metrics are incomplete")
            _validate_abs_rel_pair(
                prefix,
                abs_name,
                record[abs_name],
                rel_name,
                record[rel_name],
                atol,
                rtol,
            )


def _validate_abs_rel_pair(
    prefix: str,
    abs_name: str,
    abs_value: Any,
    rel_name: str,
    rel_value: Any,
    atol: float,
    rtol: float,
) -> None:
    max_abs = _finite_float(prefix, abs_name, abs_value)
    max_rel = _finite_float(prefix, rel_name, rel_value)
    if max_abs > atol and max_rel > rtol:
        raise ProofValidationError(
            f"{prefix}: {abs_name}/{rel_name} exceed tolerances "
            f"({max_abs:.6g} > {atol:.6g} and {max_rel:.6g} > {rtol:.6g})"
        )


def _finite_float(prefix: str, name: str, value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ProofValidationError(f"{prefix}: {name} is not numeric") from exc
    if not math.isfinite(numeric):
        raise ProofValidationError(f"{prefix}: {name} is not finite")
    return numeric


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    try:
        lines = path.read_text().splitlines()
    except OSError as exc:
        raise ProofValidationError(f"{path}: could not read proof artifact") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProofValidationError(f"{path}:{line_number}: invalid JSON") from exc
        if not isinstance(record, dict):
            raise ProofValidationError(f"{path}:{line_number}: expected JSON object")
        records.append(record)
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate FlashMask SM90 benchmark JSONL artifacts."
    )
    parser.add_argument("jsonl", nargs="+", help="JSONL artifact path")
    parser.add_argument("--min-speedup", type=float, required=True)
    parser.add_argument("--require-case", action="append", default=[])
    parser.add_argument("--no-require-speedup", action="store_true")
    args = parser.parse_args(argv)

    try:
        records = validate_sm90_proof_jsonl(
            args.jsonl,
            min_speedup=args.min_speedup,
            required_cases=set(args.require_case) if args.require_case else None,
            require_speedup=not args.no_require_speedup,
        )
    except ProofValidationError as exc:
        print(f"FlashMask SM90 proof validation failed: {exc}", file=sys.stderr)
        return 1
    print(f"validated {len(records)} FlashMask SM90 proof records")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
