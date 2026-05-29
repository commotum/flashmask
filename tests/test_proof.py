import json
import subprocess
import sys

import pytest

from flashmask import ProofValidationError, validate_sm90_proof_jsonl, validate_sm90_proof_records
from flashmask.proof import REQUIRED_FLASHMASK_CUDA_KERNEL_MARKERS


def _valid_record(**overrides):
    record = {
        "status": "ok",
        "passed": True,
        "capability": [9, 0],
        "backend": "fa3",
        "backend_kind": "sm90_sparse_fa3",
        "kernel_ready": True,
        "forward_ready": True,
        "case": "full",
        "profiler_check_skipped": False,
        "profiler_flashmask_fwd": True,
        "profiler_flashmask_cuda_kernel_events": [
            f"void {marker}(...)" for marker in REQUIRED_FLASHMASK_CUDA_KERNEL_MARKERS
        ],
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


def test_validate_sm90_proof_records_accepts_complete_artifact():
    validate_sm90_proof_records(
        [_valid_record()],
        min_speedup=1.15,
        required_cases={"full"},
    )


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
        ({"atol": None}, "atol is not numeric"),
        ({"max_abs_error": 0.04, "max_rel_error": 0.04}, "exceed tolerances"),
    ],
)
def test_validate_sm90_proof_records_rejects_incomplete_evidence(override, message):
    with pytest.raises(ProofValidationError, match=message):
        validate_sm90_proof_records([_valid_record(**override)], min_speedup=1.15)


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
