"""Dependency-free validation for FlashMask benchmark proof artifacts."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


SPARSE_SM90_FA3_BACKEND_KIND = "sm90_sparse_fa3"
SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND = "sm8x_sparse_fa2_compatible"
SPARSE_FA3_BACKEND_KIND = SPARSE_SM90_FA3_BACKEND_KIND
REQUIRED_FLASHMASK_SM90_CUDA_KERNEL_MARKERS = (
    "prepare_flashmask_kernel",
    "scanMaxMinChunkedKernel",
    "cutlass_flashmask_kernel",
)
REQUIRED_FLASHMASK_SM8X_CUDA_KERNEL_MARKERS = (
    "scanMaxMinChunkedKernel",
    "cutlass_flashmask_kernel",
)
REQUIRED_FLASHMASK_CUDA_KERNEL_MARKERS = REQUIRED_FLASHMASK_SM90_CUDA_KERNEL_MARKERS

PROOF_BACKEND_SPECS = {
    "sm90": {
        "label": "SM90",
        "capability": [9, 0],
        "backend_kind": SPARSE_SM90_FA3_BACKEND_KIND,
        "backend_names": ("fa3", "flashmask-fa3"),
        "cuda_kernel_markers": REQUIRED_FLASHMASK_SM90_CUDA_KERNEL_MARKERS,
    },
    "sm86": {
        "label": "SM86",
        "capability": [8, 6],
        "backend_kind": SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND,
        "backend_names": ("fa2-compatible", "flashmask-fa2-compatible"),
        "cuda_kernel_markers": REQUIRED_FLASHMASK_SM8X_CUDA_KERNEL_MARKERS,
    },
    "sm80": {
        "label": "SM80",
        "capability": [8, 0],
        "backend_kind": SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND,
        "backend_names": ("fa2-compatible", "flashmask-fa2-compatible"),
        "cuda_kernel_markers": REQUIRED_FLASHMASK_SM8X_CUDA_KERNEL_MARKERS,
    },
}
PROOF_BACKEND_ALIASES = {
    "fa3": "sm90",
    "fa2-compatible": "sm86",
    "sm8x-fa2-compatible": "sm86",
}


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


def validate_sm86_proof_jsonl(
    paths: list[str | Path],
    *,
    min_speedup: float,
    required_cases: set[str] | None = None,
    require_speedup: bool = True,
) -> list[dict[str, Any]]:
    """Load and validate one or more SM86 proof JSONL files."""

    return validate_proof_jsonl(
        paths,
        backend="sm86",
        min_speedup=min_speedup,
        required_cases=required_cases,
        require_speedup=require_speedup,
    )


def validate_sm80_proof_jsonl(
    paths: list[str | Path],
    *,
    min_speedup: float,
    required_cases: set[str] | None = None,
    require_speedup: bool = True,
) -> list[dict[str, Any]]:
    """Load and validate one or more SM80 proof JSONL files."""

    return validate_proof_jsonl(
        paths,
        backend="sm80",
        min_speedup=min_speedup,
        required_cases=required_cases,
        require_speedup=require_speedup,
    )


def validate_proof_jsonl(
    paths: list[str | Path],
    *,
    backend: str = "sm90",
    min_speedup: float,
    required_cases: set[str] | None = None,
    require_speedup: bool = True,
) -> list[dict[str, Any]]:
    """Load and validate one or more backend-specific proof JSONL files."""

    records: list[dict[str, Any]] = []
    for path in paths:
        records.extend(_read_jsonl(Path(path)))
    validate_proof_records(
        records,
        backend=backend,
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

    validate_proof_records(
        records,
        backend="sm90",
        min_speedup=min_speedup,
        required_cases=required_cases,
        require_speedup=require_speedup,
    )


def validate_sm86_proof_records(
    records: list[dict[str, Any]],
    *,
    min_speedup: float,
    required_cases: set[str] | None = None,
    require_speedup: bool = True,
) -> None:
    """Validate parsed benchmark records as real SM86 sparse-kernel proof."""

    validate_proof_records(
        records,
        backend="sm86",
        min_speedup=min_speedup,
        required_cases=required_cases,
        require_speedup=require_speedup,
    )


def validate_sm80_proof_records(
    records: list[dict[str, Any]],
    *,
    min_speedup: float,
    required_cases: set[str] | None = None,
    require_speedup: bool = True,
) -> None:
    """Validate parsed benchmark records as real SM80 sparse-kernel proof."""

    validate_proof_records(
        records,
        backend="sm80",
        min_speedup=min_speedup,
        required_cases=required_cases,
        require_speedup=require_speedup,
    )


def validate_proof_records(
    records: list[dict[str, Any]],
    *,
    backend: str = "sm90",
    min_speedup: float,
    required_cases: set[str] | None = None,
    require_speedup: bool = True,
) -> None:
    """Validate parsed benchmark records as real sparse-kernel proof."""

    spec = _proof_backend_spec(backend)
    if not records:
        raise ProofValidationError("proof artifact contains no records")
    if min_speedup <= 0:
        raise ProofValidationError("min_speedup must be positive")

    seen_cases: set[str] = set()
    speedup_records = 0
    for index, record in enumerate(records):
        _validate_record(index, record, min_speedup=min_speedup, spec=spec)
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


def _validate_record(
    index: int,
    record: dict[str, Any],
    *,
    min_speedup: float,
    spec: dict[str, Any],
) -> None:
    prefix = f"record {index}"
    if not isinstance(record, dict):
        raise ProofValidationError(f"{prefix}: expected JSON object")
    if record.get("status") != "ok":
        raise ProofValidationError(f"{prefix}: status is not ok")
    if record.get("passed") is not True:
        raise ProofValidationError(f"{prefix}: passed is not true")
    expected_capability = spec["capability"]
    if record.get("capability") != expected_capability:
        raise ProofValidationError(f"{prefix}: capability is not {expected_capability}")
    expected_backend_kind = spec["backend_kind"]
    if record.get("backend_kind") != expected_backend_kind:
        raise ProofValidationError(f"{prefix}: backend_kind is not {expected_backend_kind}")
    backend = record.get("backend")
    backend_names = tuple(spec["backend_names"])
    if backend not in backend_names:
        raise ProofValidationError(
            f"{prefix}: backend is not one of {', '.join(backend_names)}"
        )
    requested_backend = record.get("requested_backend")
    if requested_backend is None:
        raise ProofValidationError(f"{prefix}: requested_backend is missing")
    if requested_backend not in backend_names:
        raise ProofValidationError(
            f"{prefix}: requested_backend is not one of {', '.join(backend_names)}"
        )
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

    expected_markers = tuple(spec["cuda_kernel_markers"])
    recorded_required_markers = record.get("required_flashmask_cuda_kernel_markers")
    if recorded_required_markers is None:
        raise ProofValidationError(
            f"{prefix}: required FlashMask CUDA kernel marker list is missing"
        )
    if tuple(recorded_required_markers) != expected_markers:
        raise ProofValidationError(
            f"{prefix}: required FlashMask CUDA kernel marker list does not match backend"
        )

    kernel_events = record.get("profiler_flashmask_cuda_kernel_events")
    if not isinstance(kernel_events, list):
        raise ProofValidationError(f"{prefix}: CUDA kernel event list is missing")
    for marker in expected_markers:
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

    found_parity_pair = False
    if "max_abs_error" in record or "max_rel_error" in record:
        if "max_abs_error" not in record or "max_rel_error" not in record:
            raise ProofValidationError(f"{prefix}: PE parity abs/rel metrics are incomplete")
        found_parity_pair = True
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
            found_parity_pair = True
            _validate_abs_rel_pair(
                prefix,
                abs_name,
                record[abs_name],
                rel_name,
                record[rel_name],
                atol,
                rtol,
            )
    if not found_parity_pair:
        raise ProofValidationError(f"{prefix}: parity metrics are missing")


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


def _proof_backend_spec(backend: str) -> dict[str, Any]:
    backend = PROOF_BACKEND_ALIASES.get(backend, backend)
    try:
        return PROOF_BACKEND_SPECS[backend]
    except KeyError as exc:
        supported = ", ".join(sorted(PROOF_BACKEND_SPECS))
        raise ProofValidationError(f"unsupported proof backend {backend!r}; expected one of {supported}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate FlashMask benchmark JSONL artifacts."
    )
    parser.add_argument("jsonl", nargs="+", help="JSONL artifact path")
    parser.add_argument(
        "--backend",
        choices=tuple(sorted((*PROOF_BACKEND_SPECS, *PROOF_BACKEND_ALIASES))),
        default="sm90",
    )
    parser.add_argument("--min-speedup", type=float, required=True)
    parser.add_argument("--require-case", action="append", default=[])
    parser.add_argument("--no-require-speedup", action="store_true")
    args = parser.parse_args(argv)

    try:
        records = validate_proof_jsonl(
            args.jsonl,
            backend=args.backend,
            min_speedup=args.min_speedup,
            required_cases=set(args.require_case) if args.require_case else None,
            require_speedup=not args.no_require_speedup,
        )
    except ProofValidationError as exc:
        spec_key = PROOF_BACKEND_ALIASES.get(args.backend, args.backend)
        label = PROOF_BACKEND_SPECS.get(spec_key, {}).get("label", args.backend)
        print(f"FlashMask {label} proof validation failed: {exc}", file=sys.stderr)
        return 1
    label = PROOF_BACKEND_SPECS[PROOF_BACKEND_ALIASES.get(args.backend, args.backend)]["label"]
    print(f"validated {len(records)} FlashMask {label} proof records")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
