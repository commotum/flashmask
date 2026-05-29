import json
import subprocess
import sys

import pytest

from flashmask import (
    ProofValidationError,
    validate_proof_records,
    validate_sm80_proof_jsonl,
    validate_sm80_proof_records,
    validate_sm86_proof_jsonl,
    validate_sm86_proof_records,
    validate_sm90_proof_jsonl,
    validate_sm90_proof_records,
)
from flashmask.proof import (
    REQUIRED_FLASHMASK_CUDA_KERNEL_MARKERS,
    REQUIRED_FLASHMASK_SM8X_CUDA_KERNEL_MARKERS,
)


def _valid_record(**overrides):
    record = {
        "status": "ok",
        "passed": True,
        "capability": [9, 0],
        "backend": "fa3",
        "requested_backend": "fa3",
        "backend_kind": "sm90_sparse_fa3",
        "kernel_ready": True,
        "forward_ready": True,
        "case": "full",
        "profiler_check_skipped": False,
        "profiler_flashmask_fwd": True,
        "profiler_flashmask_cuda_kernel_events": [
            f"void {marker}(...)" for marker in REQUIRED_FLASHMASK_CUDA_KERNEL_MARKERS
        ],
        "required_flashmask_cuda_kernel_markers": list(
            REQUIRED_FLASHMASK_CUDA_KERNEL_MARKERS
        ),
        "profiler_missing_flashmask_cuda_kernel_markers": [],
        "profiler_dense_attention_events": [],
        "speedup": 1.25,
        "max_abs_error": 0.01,
        "max_rel_error": 0.01,
        "atol": 0.03,
        "rtol": 0.03,
    }
    record.update(overrides)
    return record


def _valid_sm86_record(**overrides):
    record = {
        "status": "ok",
        "passed": True,
        "capability": [8, 6],
        "backend": "flashmask-fa2-compatible",
        "requested_backend": "fa2-compatible",
        "backend_kind": "sm8x_sparse_fa2_compatible",
        "kernel_ready": True,
        "forward_ready": True,
        "case": "full",
        "profiler_check_skipped": False,
        "profiler_flashmask_fwd": True,
        "profiler_flashmask_cuda_kernel_events": [
            f"void {marker}(...)" for marker in REQUIRED_FLASHMASK_SM8X_CUDA_KERNEL_MARKERS
        ],
        "required_flashmask_cuda_kernel_markers": list(
            REQUIRED_FLASHMASK_SM8X_CUDA_KERNEL_MARKERS
        ),
        "profiler_missing_flashmask_cuda_kernel_markers": [],
        "profiler_dense_attention_events": [],
        "speedup": 1.9,
        "max_abs_error": 0.003,
        "max_rel_error": 200.0,
        "atol": 0.06,
        "rtol": 0.06,
    }
    record.update(overrides)
    return record


def _valid_sm80_record(**overrides):
    return _valid_sm86_record(capability=[8, 0], **overrides)


def test_validate_sm90_proof_records_accepts_complete_artifact():
    validate_sm90_proof_records(
        [_valid_record()],
        min_speedup=1.15,
        required_cases={"full"},
    )


def test_validate_sm86_proof_records_accepts_backend_specific_artifact():
    validate_sm86_proof_records(
        [_valid_sm86_record()],
        min_speedup=1.5,
        required_cases={"full"},
    )


def test_validate_sm80_proof_records_accepts_backend_specific_artifact():
    validate_sm80_proof_records(
        [_valid_sm80_record()],
        min_speedup=1.5,
        required_cases={"full"},
    )


def test_validate_proof_records_dispatches_by_backend():
    validate_proof_records([_valid_sm86_record()], backend="fa2-compatible", min_speedup=1.5)
    validate_proof_records([_valid_sm80_record()], backend="sm80", min_speedup=1.5)


def test_validate_sm86_proof_does_not_require_sm90_prepare_kernel_marker():
    validate_sm86_proof_records(
        [
            _valid_sm86_record(
                profiler_flashmask_cuda_kernel_events=[
                    "void scanMaxMinChunkedKernel(...)",
                    "void cutlass_flashmask_kernel(...)",
                ],
            )
        ],
        min_speedup=1.5,
    )


def test_validate_sm86_proof_rejects_wrong_backend_kind():
    with pytest.raises(ProofValidationError, match="backend_kind is not sm8x_sparse_fa2_compatible"):
        validate_sm86_proof_records(
            [_valid_sm86_record(backend_kind="sm90_sparse_fa3")],
            min_speedup=1.5,
        )


def test_validate_proof_rejects_wrong_required_marker_list():
    with pytest.raises(ProofValidationError, match="marker list does not match backend"):
        validate_sm86_proof_records(
            [
                _valid_sm86_record(
                    required_flashmask_cuda_kernel_markers=[
                        "prepare_flashmask_kernel",
                        "scanMaxMinChunkedKernel",
                        "cutlass_flashmask_kernel",
                    ],
                )
            ],
            min_speedup=1.5,
        )


def test_validate_proof_rejects_missing_parity_metrics():
    record = _valid_sm86_record()
    del record["max_abs_error"]
    del record["max_rel_error"]
    with pytest.raises(ProofValidationError, match="parity metrics are missing"):
        validate_sm86_proof_records([record], min_speedup=1.5)


@pytest.mark.parametrize(
    "override, message",
    [
        ({"status": "skipped"}, "status is not ok"),
        ({"capability": [8, 6]}, "capability is not"),
        ({"profiler_check_skipped": True}, "profiler check was skipped"),
        ({"profiler_missing_flashmask_cuda_kernel_markers": ["cutlass_flashmask_kernel"]}, "markers are missing"),
        ({"profiler_dense_attention_events": ["aten::matmul"]}, "dense attention events"),
        ({"speedup": 1.0}, "below required"),
        ({"backend": None}, "backend is not"),
        ({"requested_backend": None}, "requested_backend is missing"),
        ({"requested_backend": "fa2-compatible"}, "requested_backend is not"),
        ({"required_flashmask_cuda_kernel_markers": None}, "marker list is missing"),
        ({"atol": None}, "atol is not numeric"),
        ({"max_abs_error": 0.04, "max_rel_error": 0.04}, "exceed tolerances"),
    ],
)
def test_validate_sm90_proof_records_rejects_incomplete_evidence(override, message):
    with pytest.raises(ProofValidationError, match=message):
        validate_sm90_proof_records([_valid_record(**override)], min_speedup=1.15)


def test_validate_sm86_proof_records_rejects_missing_sm8x_kernel_marker():
    with pytest.raises(ProofValidationError, match="missing CUDA kernel event marker scanMaxMinChunkedKernel"):
        validate_sm86_proof_records(
            [
                _valid_sm86_record(
                    profiler_flashmask_cuda_kernel_events=["void cutlass_flashmask_kernel(...)"],
                )
            ],
            min_speedup=1.5,
        )


def test_validate_sm90_proof_jsonl_loads_multiple_artifacts(tmp_path):
    left = tmp_path / "left.jsonl"
    right = tmp_path / "right.jsonl"
    left.write_text(json.dumps(_valid_record(case="full")) + "\n")
    right.write_text(json.dumps(_valid_record(case="query")) + "\n")

    records = validate_sm90_proof_jsonl(
        [left, right],
        min_speedup=1.15,
        required_cases={"full", "query"},
    )

    assert len(records) == 2


def test_validate_sm86_proof_jsonl_loads_artifact(tmp_path):
    path = tmp_path / "sm86.jsonl"
    path.write_text(json.dumps(_valid_sm86_record()) + "\n")

    records = validate_sm86_proof_jsonl([path], min_speedup=1.5, required_cases={"full"})

    assert len(records) == 1


def test_validate_sm80_proof_jsonl_loads_artifact(tmp_path):
    path = tmp_path / "sm80.jsonl"
    path.write_text(json.dumps(_valid_sm80_record()) + "\n")

    records = validate_sm80_proof_jsonl([path], min_speedup=1.5, required_cases={"full"})

    assert len(records) == 1


def test_validate_sm90_proof_cli(tmp_path):
    path = tmp_path / "proof.jsonl"
    path.write_text(json.dumps(_valid_record()) + "\n")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "flashmask.proof",
            str(path),
            "--min-speedup",
            "1.15",
            "--require-case",
            "full",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "validated 1 FlashMask SM90 proof records" in result.stdout


def test_validate_sm86_proof_cli(tmp_path):
    path = tmp_path / "proof-sm86.jsonl"
    path.write_text(json.dumps(_valid_sm86_record()) + "\n")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "flashmask.proof",
            str(path),
            "--backend",
            "fa2-compatible",
            "--min-speedup",
            "1.5",
            "--require-case",
            "full",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "validated 1 FlashMask SM86 proof records" in result.stdout


def test_validate_sm80_proof_cli(tmp_path):
    path = tmp_path / "proof-sm80.jsonl"
    path.write_text(json.dumps(_valid_sm80_record()) + "\n")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "flashmask.proof",
            str(path),
            "--backend",
            "sm80",
            "--min-speedup",
            "1.5",
            "--require-case",
            "full",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "validated 1 FlashMask SM80 proof records" in result.stdout
