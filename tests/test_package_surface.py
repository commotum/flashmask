from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
import textwrap
import zipfile
from pathlib import Path

import pytest

import flashmask
import flashmask.attention as attention_module
from flashmask._backend import (
    ExtensionStatus,
    SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND,
    SPARSE_SM90_FA3_BACKEND_KIND,
    extension_status,
)


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


def _blocked_import_names() -> tuple[str, ...]:
    return (
        "torch",
        "Pa" + "ddle",
        "Pa" + "ddle" + "NLP",
        "pa" + "ddle",
        "pa" + "ddlenlp",
    )


def test_import_keeps_surface_small():
    script = textwrap.dedent(
        f"""
        import builtins
        import sys

        blocked = { _blocked_import_names()!r}
        attempts = []
        real_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name in blocked or any(name.startswith(prefix + ".") for prefix in blocked):
                attempts.append(name)
                raise RuntimeError("blocked eager import: " + name)
            return real_import(name, *args, **kwargs)

        builtins.__import__ = guarded_import
        import flashmask
        loaded = [name for name in blocked if name in sys.modules]
        raise SystemExit(1 if attempts or loaded else 0)
        """
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_package_metadata_has_no_runtime_dependencies():
    root = Path(__file__).resolve().parents[1]
    pyproject_text = (root / "pyproject.toml").read_text()

    assert "dependencies = []" in pyproject_text
    assert "torch" not in pyproject_text.split("[dependency-groups]", 1)[0]


def test_distribution_manifest_excludes_reference_material():
    root = Path(__file__).resolve().parents[1]
    manifest_text = (root / "MANIFEST.in").read_text()
    pyproject_text = (root / "pyproject.toml").read_text()

    assert "recursive-include src/flashmask/csrc *" in manifest_text
    assert "include-package-data = false" in pyproject_text
    assert 'exclude = ["flashmask.csrc*"]' in pyproject_text
    for directory in ("context", "documentation", "paper", "sub", "tests", "uv-docs"):
        assert f"prune {directory}" in manifest_text
    assert "exclude summary_flashmask.md" in manifest_text


def test_distribution_artifacts_have_expected_boundaries(tmp_path):
    root = Path(__file__).resolve().parents[1]
    out_dir = tmp_path / "dist"
    try:
        result = subprocess.run(
            ["uv", "build", "--sdist", "--wheel", "--out-dir", str(out_dir)],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

        sdist = next(out_dir.glob("flashmask-*.tar.gz"))
        wheel = next(out_dir.glob("flashmask-*.whl"))
        with tarfile.open(sdist) as archive:
            sdist_names = set(archive.getnames())
        with zipfile.ZipFile(wheel) as archive:
            wheel_names = set(archive.namelist())

        assert any(name.endswith("src/flashmask/csrc/flashmask_experimental.cu") for name in sdist_names)
        assert any(name.endswith("src/flashmask/csrc/flashmask_api.cpp") for name in sdist_names)
        assert all("flashmask/csrc/" not in name for name in wheel_names)

        forbidden_parts = {"context", "documentation", "paper", "sub", "tests", "uv-docs"}
        for names in (sdist_names, wheel_names):
            for name in names:
                assert not (set(Path(name).parts) & forbidden_parts), name
                assert "summary_flashmask" not in name
    finally:
        shutil.rmtree(root / "build", ignore_errors=True)
        shutil.rmtree(root / "src" / "flashmask.egg-info", ignore_errors=True)


def test_sm90_benchmark_harness_is_lazy_and_scripted():
    root = Path(__file__).resolve().parents[1]
    bench_text = (root / "src" / "flashmask" / "bench_sm90.py").read_text()
    pyproject_text = (root / "pyproject.toml").read_text()
    readme_text = (root / "README.md").read_text()

    assert 'flashmask-bench-sm90 = "flashmask.bench_sm90:main"' in pyproject_text
    assert 'flashmask-validate-sm90-proof = "flashmask.proof:main"' in pyproject_text
    assert "uv run flashmask-bench-sm90" in readme_text
    assert "uv run flashmask-validate-sm90-proof" in readme_text
    assert "def _import_torch():" in bench_text
    assert "import torch" not in bench_text.split("def _import_torch():", 1)[0]
    assert "\"backend\": \"fa3\"" in bench_text
    assert "\"requested_backend\": \"fa3\"" in bench_text
    assert "\"selected_backend\"" in bench_text
    assert "\"forward_ready\"" in bench_text
    assert "\"backward_ready\"" in bench_text
    assert "dense_sdpa_ms" in bench_text
    assert "\"speedup\"" in bench_text
    assert "\"flashmask_api_ms\"" in bench_text
    assert "\"flashmask_raw_ms\"" in bench_text
    assert "\"raw_speedup\"" in bench_text
    assert "compile_pe_state_causal_mask" in bench_text
    assert "compile_pe_state_causal_query_mask" in bench_text
    assert "torch.ops.flashmask.fwd" in bench_text
    assert "result.backend != \"fa3\"" in bench_text
    assert "def _profile_sparse_attention" in bench_text
    assert "torch.profiler.profile" in bench_text
    assert "flashmask::fwd" in bench_text
    assert "REQUIRED_FLASHMASK_CUDA_KERNEL_MARKERS" in bench_text
    assert "prepare_flashmask_kernel" in bench_text
    assert "scanMaxMinChunkedKernel" in bench_text
    assert "cutlass_flashmask_kernel" in bench_text
    assert "\"profiler_flashmask_cuda_kernel_events\"" in bench_text
    assert "\"profiler_missing_flashmask_cuda_kernel_markers\"" in bench_text
    assert "\"required_flashmask_cuda_kernel_markers\"" in bench_text
    assert "scaled_dot_product_attention" in bench_text
    assert "\"profiler_dense_attention_events\"" in bench_text
    assert "\"profiler_flashmask_fwd\"" in bench_text
    assert "--skip-profiler-check" in bench_text
    assert "\"status\": \"skipped\"" in bench_text
    assert "\"allowed_density\"" in bench_text
    assert "--min-speedup" in bench_text
    assert "--min-speedup is required for benchmark mode" in bench_text
    assert "--output-jsonl" in bench_text
    assert "def _emit_records" in bench_text
    assert "def _failed_record" in bench_text
    assert "\"profiler_check_skipped\"" in bench_text
    assert "--skip-profiler-check cannot be used with --require-sm90" in bench_text
    assert "--min-speedup must be positive" in bench_text
    assert "--bench-seq-lens values must be at least 3" in bench_text
    assert "--pad-fractions must contain at least one value" in bench_text
    assert "--head-dims values must be in [1, 128]" in bench_text
    assert "def _validate_args" in bench_text
    assert "def _mask_digest" in bench_text
    assert "torch.cuda.Event" in bench_text


def test_sm90_benchmark_can_write_jsonl_artifact(tmp_path, capsys):
    from flashmask import bench_sm90

    output = tmp_path / "bench" / "records.jsonl"
    bench_sm90._emit_records(
        [{"status": "ok", "speedup": 1.25}],
        jsonl=True,
        output_jsonl=str(output),
    )

    captured = capsys.readouterr()
    assert '"speedup": 1.25' in captured.out
    assert output.read_text() == '{"speedup": 1.25, "status": "ok"}\n'


def test_sm90_benchmark_rejects_unsupported_head_dim_before_torch_import():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "flashmask.bench_sm90",
            "--mode",
            "parity",
            "--head-dims",
            "256",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "--head-dims values must be in [1, 128]" in result.stderr


def test_sm90_benchmark_rejects_nonpositive_speedup_before_torch_import():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "flashmask.bench_sm90",
            "--mode",
            "bench",
            "--head-dims",
            "128",
            "--bench-seq-lens",
            "2048",
            "--min-speedup",
            "0",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "--min-speedup must be positive" in result.stderr


def test_sm90_benchmark_rejects_required_run_without_profiler_before_torch_import():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "flashmask.bench_sm90",
            "--mode",
            "parity",
            "--require-sm90",
            "--skip-profiler-check",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "--skip-profiler-check cannot be used with --require-sm90" in result.stderr


def test_optional_cuda_tests_have_required_sm90_failure_gate():
    root = Path(__file__).resolve().parents[1]
    optional_test_text = (
        (root / "tests" / "test_cuda_extension_optional.py").read_text()
        + (root / "tests" / "test_cuda_pe_parity_optional.py").read_text()
    )

    assert "FLASHMASK_REQUIRE_SM90" in optional_test_text
    assert "def _require_or_skip" in optional_test_text
    assert "pytest.importorskip(\"torch\")" not in optional_test_text


def test_attention_backend_routes_to_flashmask_op_not_dense_sdpa():
    root = Path(__file__).resolve().parents[1]
    backend_text = (root / "src" / "flashmask" / "_backend.py").read_text()
    api_text = (root / "src" / "flashmask" / "csrc" / "flashmask_api.cpp").read_text()

    sparse_body = backend_text.split("def sparse_attention_forward(", 1)[1].split(
        "\ndef _validate_interval_mask_call",
        1,
    )[0]
    assert "return torch.ops.flashmask.fwd(" in sparse_body
    assert "scaled_dot_product_attention" not in backend_text
    assert 'm.impl("fwd", &flashmask_fwd)' in api_text
    assert "return flashmask_fwd_cuda(" in _function_body(api_text, "flashmask_fwd")


def test_attention_interface_is_not_dense_fallback(monkeypatch):
    monkeypatch.setattr(
        attention_module,
        "extension_status",
        lambda: ExtensionStatus(
            loaded=False,
            kernel_ready=False,
            unavailable_reason="forced unavailable",
        ),
    )
    mask = flashmask.IntervalMask([[[[1]]]], causal=True)

    with pytest.raises(NotImplementedError):
        flashmask.flashmask_attention(None, None, None, mask)


def test_backend_verification_fails_closed_until_sparse_fa3_exists(monkeypatch):
    monkeypatch.setattr(
        attention_module,
        "extension_status",
        lambda: ExtensionStatus(
            loaded=False,
            kernel_ready=False,
            unavailable_reason="forced unavailable",
        ),
    )
    info = flashmask.backend_info()

    assert info.available is False
    assert info.is_fa3 is False
    assert info.supports_sparse_mask is False
    assert info.training_available is False
    assert info.supports_block_mask is False
    assert info.supports_native_gqa is False
    assert info.supported_dtypes == ()
    assert info.max_head_dim is None
    assert info.backend_kind is None

    with pytest.raises(RuntimeError):
        flashmask.verify_backend()


def test_sparse_backend_kind_constants_distinguish_sm90_fa3_from_sm8x_fa2():
    assert flashmask.SPARSE_SM90_FA3_BACKEND_KIND == "sm90_sparse_fa3"
    assert (
        flashmask.SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND
        == "sm8x_sparse_fa2_compatible"
    )
    assert flashmask.SPARSE_SM90_FA3_BACKEND_KIND == SPARSE_SM90_FA3_BACKEND_KIND
    assert (
        flashmask.SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND
        == SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND
    )
    assert flashmask.SUPPORTED_SPARSE_BACKEND_KINDS == frozenset(
        {
            SPARSE_SM90_FA3_BACKEND_KIND,
            SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND,
        }
    )
    assert flashmask.REPRESENTABLE_SPARSE_BACKEND_KINDS == frozenset(
        {
            SPARSE_SM90_FA3_BACKEND_KIND,
            SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND,
        }
    )
    assert SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND in flashmask.SUPPORTED_SPARSE_BACKEND_KINDS


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
    assert 'm.def("backend_kind"' in api_text
    assert 'm.def("forward_ready"' in api_text
    assert 'm.def("backward_ready"' in api_text
    assert 'm.def("cuda_available"' in api_text
    assert 'm.def("current_compute_capability"' in api_text
    assert 'm.def("supported_compute_capabilities"' in api_text
    assert "sm90_sparse_fa3" in api_text
    assert "stub" in api_text
    assert stub.exists()
    assert experimental.exists()


def test_cuda_backend_readiness_uses_exact_device_gates():
    root = Path(__file__).resolve().parents[1]
    api_text = (root / "src" / "flashmask" / "csrc" / "flashmask_api.cpp").read_text()
    bench_text = (root / "src" / "flashmask" / "bench_sm90.py").read_text()

    assert "prop.major == 9 && prop.minor == 0" in api_text
    assert "prop.major == 8 && (prop.minor == 0 || prop.minor == 6)" in api_text
    assert "tuple(capability) != (9, 0)" in bench_text
    assert "capability[0] != 9" not in bench_text
    assert "capability[0] == 9" not in bench_text


def test_experimental_forward_wrapper_is_sm90_gated():
    root = Path(__file__).resolve().parents[1]
    experimental_text = (
        root / "src" / "flashmask" / "csrc" / "flashmask_experimental.cu"
    ).read_text()

    assert "experimental FlashMask forward requires an SM90 / compute capability 9.0 GPU" in experimental_text
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


def test_sm90_fa3_static_path_requires_flashmask_metadata_dispatch():
    root = Path(__file__).resolve().parents[1]
    experimental_text = (
        root / "src" / "flashmask" / "csrc" / "flashmask_experimental.cu"
    ).read_text()
    launch_text = (
        root
        / "src"
        / "flashmask"
        / "csrc"
        / "flashmask_v2"
        / "flash_fwd_launch_template.h"
    ).read_text()

    assert (
        "set_startend_ptrs(params, startend_row_indices, causal, owned_bounds);"
        in experimental_text
    )
    assert "BOOL_SWITCH(params.lt_start_ptr != nullptr, Is_flashmask" in launch_text
    assert "Is_flashmask && !Is_FP8" in launch_text
    assert (
        "flash::flashmask::prepare_block_maxmin<kBlockN>"
        "(params, scaled_seqlen_k, stream, true);"
        in launch_text
    )
    assert (
        "prepare_flashmask(params, stream, params.num_sm, Scheduler::pipelining);"
        in launch_text
    )
    assert "CollectiveMainloopFwdSm90<" in launch_text


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


def test_sm80_sparse_path_threads_flashmask_metadata():
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
    assert "SM80/86 FlashMask sparse forward currently supports only dense Q/K/V" in mainloop_text
    assert "flashmask_apply_direct" in mainloop_text
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
    assert "flashmask_previous_unmasked_n_block" in mainloop_text
    assert "n_block = flashmask_previous_unmasked_n_block(n_block)" in mainloop_text
    assert (
        "next_n_block_to_load = flashmask_previous_unmasked_n_block(n_block - kStages)"
        in mainloop_text
    )
    assert "for (; n_block >= n_block_min; n_block = next_n_block_to_load)" in mainloop_text
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
    assert "dense forward path" not in setup_text
    assert "FLASHMASK_BUILD_EXPERIMENTAL_SM8X_CUDA" in setup_text
    assert "FLASHMASK_SM8X_V2_BUILD=1" in setup_text
    assert "flash_fwd_hdim96_bf16_sm80.cu" in setup_text
    assert "flash_fwd_hdim96_fp16_sm80.cu" in setup_text
    assert "flash_fwd_hdim128_bf16_sm80.cu" in setup_text
    assert "flash_fwd_hdim128_fp16_sm80.cu" in setup_text
    assert "flash_fwd_hdim96_bf16_sm86.cu" in setup_text
    assert "flash_fwd_hdim96_fp16_sm86.cu" in setup_text
    assert "flash_fwd_hdim128_bf16_sm86.cu" in setup_text
    assert "flash_fwd_hdim128_fp16_sm86.cu" in setup_text
    assert "FLASHMASK_SM8X_KERNEL_READY=1" in setup_text
    assert "compute_80" in setup_text
    assert "code=sm_80" in setup_text
    assert "compute_86" in setup_text
    assert "code=sm_86" in setup_text


def test_sm8x_instantiations_include_sm80_and_sm86_dispatch_targets():
    root = Path(__file__).resolve().parents[1]
    instantiation_dir = (
        root / "src" / "flashmask" / "csrc" / "flashmask_v2" / "instantiations"
    )
    for sm in ("80", "86"):
        for head_dim in ("96", "128"):
            for dtype in ("bf16", "fp16"):
                path = instantiation_dir / f"flash_fwd_hdim{head_dim}_{dtype}_sm{sm}.cu"
                text = path.read_text()
                assert f"    {sm}," in text
                assert f"    {head_dim}," in text

    wrapper_text = (
        root / "src" / "flashmask" / "csrc" / "flashmask_experimental.cu"
    ).read_text()
    assert "arch == 80 || arch == 86" in wrapper_text
    assert "run_sm8x_mha_fwd" in wrapper_text
    assert "run_mha_fwd_<80, T, kHeadDim" in wrapper_text
    assert "run_mha_fwd_<86, T, kHeadDim" in wrapper_text


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
            _blocked = ("torch", "pa" + "ddle", "pa" + "ddlenlp", "Pa" + "ddle", "Pa" + "ddle" + "NLP")

            def _guarded_import(name, *args, **kwargs):
                if name in _blocked or any(name.startswith(prefix + ".") for prefix in _blocked):
                    raise RuntimeError("blocked metadata import: " + name)
                return _real_import(name, *args, **kwargs)

            builtins.__import__ = _guarded_import
            """
        )
    )
    env = os.environ.copy()
    env.pop("FLASHMASK_BUILD_CUDA", None)
    env.pop("FLASHMASK_BUILD_EXPERIMENTAL_CUDA", None)
    env.pop("FLASHMASK_BUILD_EXPERIMENTAL_SM8X_CUDA", None)
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


def test_setup_cuda_build_modes_are_mutually_exclusive(tmp_path):
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "setuptools.py").write_text("def setup(**kwargs): pass\n")
    env = os.environ.copy()
    env["FLASHMASK_BUILD_CUDA"] = "1"
    env["FLASHMASK_BUILD_EXPERIMENTAL_CUDA"] = "1"
    env.pop("FLASHMASK_BUILD_EXPERIMENTAL_SM8X_CUDA", None)
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

    assert result.returncode != 0
    assert "Set only one FlashMask CUDA build mode" in result.stderr


def test_extension_status_is_lazy():
    status = extension_status()

    assert isinstance(status.loaded, bool)
    assert isinstance(status.kernel_ready, bool)
    assert status.cuda_available in (None, True, False)
    assert status.compute_capability is None or (
        isinstance(status.compute_capability, tuple)
        and len(status.compute_capability) == 2
        and all(isinstance(value, int) for value in status.compute_capability)
    )
    assert isinstance(status.supported_compute_capabilities, tuple)
    assert all(
        isinstance(value, tuple)
        and len(value) == 2
        and all(isinstance(part, int) for part in value)
        for value in status.supported_compute_capabilities
    )
    if status.kernel_ready:
        assert status.loaded is True
        assert status.backend_kind in {
            SPARSE_SM90_FA3_BACKEND_KIND,
            SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND,
        }
        assert status.forward_ready is True
        if status.backend_kind == SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND:
            assert isinstance(status.backward_ready, bool)
        else:
            assert status.backward_ready is False
    else:
        assert status.unavailable_reason is not None
