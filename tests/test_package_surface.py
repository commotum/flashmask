from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import flashmask
from flashmask._backend import extension_status


def _function_body(text: str, name: str) -> str:
    start = text.index(f"{name}(")
    brace = text.index("{", start)
    depth = 0
    for idx in range(brace, len(text)):
        char = text[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[brace : idx + 1]
    raise AssertionError(f"could not find function body for {name}")


def test_import_keeps_surface_small():
    assert "torch" not in sys.modules


def test_sm90_benchmark_harness_is_lazy_and_scripted():
    root = Path(__file__).resolve().parents[1]
    bench_text = (root / "src" / "flashmask" / "bench_sm90.py").read_text()
    pyproject_text = (root / "pyproject.toml").read_text()
    readme_text = (root / "README.md").read_text()

    assert 'flashmask-bench-sm90 = "flashmask.bench_sm90:main"' in pyproject_text
    assert "uv run flashmask-bench-sm90" in readme_text
    assert "def _import_torch():" in bench_text
    assert "import torch" not in bench_text.split("def _import_torch():", 1)[0]
    assert "\"backend\": \"fa3\"" in bench_text
    assert "dense_sdpa_ms" in bench_text
    assert "\"speedup\"" in bench_text
    assert "compile_pe_state_causal_mask" in bench_text
    assert "compile_pe_state_causal_query_mask" in bench_text
    assert "torch.ops.flashmask.fwd" in bench_text
    assert "result.backend != \"fa3\"" in bench_text
    assert "def _profile_has_flashmask_fwd" in bench_text
    assert "torch.profiler.profile" in bench_text
    assert "flashmask::fwd" in bench_text
    assert "\"profiler_flashmask_fwd\"" in bench_text
    assert "--skip-profiler-check" in bench_text
    assert "\"status\": \"skipped\"" in bench_text
    assert "\"allowed_density\"" in bench_text
    assert "--min-speedup" in bench_text
    assert "torch.cuda.Event" in bench_text


def test_attention_interface_is_not_dense_fallback():
    mask = flashmask.IntervalMask([[[[1]]]], causal=True)

    with pytest.raises(NotImplementedError):
        flashmask.flashmask_attention(None, None, None, mask)


def test_backend_verification_fails_closed_until_sparse_fa3_exists():
    info = flashmask.backend_info()

    assert info.available is False
    assert info.is_fa3 is True
    assert info.supports_sparse_mask is False

    with pytest.raises(RuntimeError):
        flashmask.verify_backend()


def test_cuda_backend_scaffold_declares_final_op_surface():
    root = Path(__file__).resolve().parents[1]
    api = root / "src" / "flashmask" / "csrc" / "flashmask_api.cpp"
    stub = root / "src" / "flashmask" / "csrc" / "flashmask_stub.cu"
    experimental = root / "src" / "flashmask" / "csrc" / "flashmask_experimental.cu"

    api_text = api.read_text()
    assert 'm.def("fwd(' in api_text
    assert 'm.def("bwd(' in api_text
    assert "startend_row_indices" in api_text
    assert "FLASHMASK_KERNEL_READY" in api_text
    assert stub.exists()
    assert experimental.exists()


def test_experimental_forward_wrapper_is_sm90_gated():
    root = Path(__file__).resolve().parents[1]
    experimental_text = (
        root / "src" / "flashmask" / "csrc" / "flashmask_experimental.cu"
    ).read_text()

    assert "experimental FlashMask forward requires an SM90 GPU" in experimental_text
    assert "run_mha_fwd_<90, cutlass::bfloat16_t, 96, 96" in experimental_text
    assert "run_mha_fwd_<90, cutlass::half_t, 96, 96" in experimental_text
    assert "run_mha_fwd_<90, cutlass::bfloat16_t, 128, 128" in experimental_text
    assert "run_mha_fwd_<90, cutlass::half_t, 128, 128" in experimental_text
    assert "experimental forward supports head_dim <= 128 only" in experimental_text
    assert "params.m_block_dim = 128" in experimental_text
    assert "params.n_block_dim = 128" in experimental_text
    assert "FLASHMASK_VALIDATE_RAW_OP" in experimental_text
    assert "raw_startend_validation_enabled()" in experimental_text
    assert "validate_startend_debug(q, startend_row_indices, causal);" in experimental_text
    assert "must be on the same CUDA device as q" in experimental_text
    assert "block_mask yet" in experimental_text


def test_experimental_raw_op_sync_validation_is_debug_gated():
    root = Path(__file__).resolve().parents[1]
    experimental_text = (
        root / "src" / "flashmask" / "csrc" / "flashmask_experimental.cu"
    ).read_text()

    debug_body = _function_body(experimental_text, "validate_startend_debug")
    check_body = _function_body(experimental_text, "check_forward_inputs")
    forward_body = _function_body(experimental_text, "flashmask_fwd_cuda")

    assert "startend_row_indices.min().item" in debug_body
    assert "startend_row_indices.max().item" in debug_body
    assert ".any().item<bool>()" in _function_body(experimental_text, "any_true_sync")
    assert "if (raw_startend_validation_enabled())" in check_body
    assert "validate_startend_debug(q, startend_row_indices, causal);" in check_body
    assert ".item<" not in check_body.replace("validate_startend_debug(q, startend_row_indices, causal);", "")
    assert ".item<" not in forward_body
    assert "cudaDeviceSynchronize" not in experimental_text
    assert "cudaStreamSynchronize" not in experimental_text
    assert "cudaMemcpy" not in experimental_text


def test_raw_torch_ops_are_documented_as_internal_abi():
    root = Path(__file__).resolve().parents[1]
    readme_text = (root / "src" / "flashmask" / "csrc" / "README.md").read_text()
    init_text = (root / "src" / "flashmask" / "__init__.py").read_text()

    assert "low-level `torch.ops.flashmask.*` entry points assume callers pass" in readme_text
    assert "prevalidated mask metadata" in readme_text
    assert "flashmask_attention" in readme_text
    assert "torch.ops.flashmask" not in init_text


def test_sm80_sparse_path_is_compile_gated_until_ported():
    root = Path(__file__).resolve().parents[1]
    mainloop_text = (
        root / "src" / "flashmask" / "csrc" / "flashmask_v2" / "mainloop_fwd_sm80.hpp"
    ).read_text()
    launch_text = (
        root
        / "src"
        / "flashmask"
        / "csrc"
        / "flashmask_v2"
        / "flash_fwd_launch_template.h"
    ).read_text()

    assert "bool Is_flashmask_ = false" in mainloop_text
    assert "static constexpr bool Is_flashmask = Is_flashmask_" in mainloop_text
    assert "SM80/86 FlashMask sparse forward requires porting" in mainloop_text
    assert "int const h_flashmask;" in mainloop_text
    assert "int const h_h_flashmask_ratio;" in mainloop_text
    assert "int32_t* __restrict__ const lt_start_ptr = nullptr;" in mainloop_text
    assert "int32_t* __restrict__ const ut_end_nblockmin = nullptr;" in mainloop_text
    assert "int const m_block_dim;" in mainloop_text
    assert "int const n_block_dim;" in mainloop_text
    assert "int32_t* __restrict__ const block_mask_ptr = nullptr;" in mainloop_text
    assert "int const* __restrict__ const write_ptr = nullptr;" in mainloop_text
    assert "int const kv_chunk_size = 8192;" in mainloop_text
    assert "args.h_flashmask, args.h_h_flashmask_ratio" in mainloop_text
    assert "args.m_block_dim, args.n_block_dim" in mainloop_text
    assert "args.block_mask_ptr" in mainloop_text
    assert "args.write_ptr" in mainloop_text
    assert "args.kv_chunk_size" in mainloop_text
    assert "cute::bool_constant<Is_flashmask>{} /*check_inf*/" in mainloop_text
    assert (
        "CollectiveMainloopFwdSm80<kNWarps, kStages, Q_in_regs, TileShape_MNK,"
        in launch_text
    )
    assert "PackGQA, Split, Is_flashmask>" in launch_text


def test_setup_declares_experimental_sm90_kernel_build_surface():
    root = Path(__file__).resolve().parents[1]
    setup_text = (root / "setup.py").read_text()

    assert "FLASHMASK_BUILD_EXPERIMENTAL_CUDA" in setup_text
    assert "FLASHMASK_KERNEL_READY=1" in setup_text
    assert "flashmask_experimental.cu" in setup_text
    assert "flash_prepare_scheduler.cu" in setup_text
    assert "flash_fwd_hdim96_bf16_sm90.cu" in setup_text
    assert "flash_fwd_hdim96_fp16_sm90.cu" in setup_text
    assert "flash_fwd_hdim128_bf16_sm90.cu" in setup_text
    assert "flash_fwd_hdim128_fp16_sm90.cu" in setup_text
    assert "FLASHMASK_V2_DISABLE_SM8x" in setup_text
    assert "FLASHMASK_V2_DISABLE_BACKWARD" in setup_text
    assert "FLASHMASK_V2_DISABLE_FP8" in setup_text
    assert "FLASHMASK_V2_DISABLE_HDIM64" in setup_text
    assert "FLASHMASK_V2_DISABLE_HDIM192" in setup_text
    assert "FLASHMASK_V2_DISABLE_HDIM256" in setup_text
    assert "compute_90a" in setup_text
    assert "dense forward path" in setup_text
    assert "sm8x.cu" not in setup_text


def test_setup_normal_metadata_does_not_import_torch(tmp_path):
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "setuptools.py").write_text(
        textwrap.dedent(
            """
            def setup(**kwargs):
                print("setup-called")
            """
        )
    )
    (tmp_path / "sitecustomize.py").write_text(
        textwrap.dedent(
            """
            import builtins

            _real_import = builtins.__import__

            def _guarded_import(name, *args, **kwargs):
                if name == "torch" or name.startswith("torch."):
                    raise RuntimeError("torch import is blocked for this test")
                return _real_import(name, *args, **kwargs)

            builtins.__import__ = _guarded_import
            """
        )
    )
    env = os.environ.copy()
    env.pop("FLASHMASK_BUILD_CUDA", None)
    env.pop("FLASHMASK_BUILD_EXPERIMENTAL_CUDA", None)
    pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(tmp_path) if not pythonpath else f"{tmp_path}{os.pathsep}{pythonpath}"

    result = subprocess.run(
        [sys.executable, "setup.py"],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "setup-called"


def test_extension_status_is_lazy_and_unavailable_without_build():
    status = extension_status()

    assert status.loaded is False
    assert status.kernel_ready is False
