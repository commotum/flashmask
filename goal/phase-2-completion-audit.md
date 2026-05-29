# Phase 2 Completion Audit

Goal: prove the standalone PyTorch extension ABI is locked down and tested
against `/home/jake/Developer/flashmask/goal/phase-2-pytorch-extension-abi.md`.

## Exit Evidence

- Python API and raw torch op signatures:
  - Evidence:
    `/home/jake/Developer/flashmask/src/flashmask/attention.py`
    `/home/jake/Developer/flashmask/src/flashmask/_backend.py`
    `/home/jake/Developer/flashmask/src/flashmask/csrc/flashmask_api.cpp`
  - Result: complete. The public API is `flashmask_attention(...)`; raw ops are
    `torch.ops.flashmask.fwd` and `torch.ops.flashmask.bwd`. Backend selection
    is validated in Python before dispatch. The raw ops do not accept a backend
    string.

- Implemented raw op signatures match the Phase 2 ABI:
  - Evidence:
    `tests/test_package_surface.py::test_cuda_backend_scaffold_declares_final_op_surface`
    and `tests/test_backend_contract.py::test_flashmask_attention_calls_sparse_torch_op_once`.
  - Result: complete. The C++ registration exposes the documented tensor-only
    ABI, and the Python wrapper calls the raw op with `(q, k, v, startend,
    block_mask, softmax_scale, causal)`.

- Stub/no-extension imports:
  - Evidence:
    `uv run pytest -q` passed, including package import tests.
  - Runtime check:
    `PYTHONPATH=/home/jake/Developer/flashmask/src uv run python -c 'import flashmask'`
    imported `/home/jake/Developer/flashmask/src/flashmask/__init__.py`.
  - Result: complete. The package imports without requiring a working CUDA
    extension.

- Fail-closed behavior:
  - Evidence:
    `tests/test_backend_contract.py`, `tests/test_package_surface.py`, and
    `tests/test_cuda_extension_optional.py`.
  - Result: complete. Missing extension, stub backend, wrong backend kind,
    missing backward, invalid interval metadata, unsupported head dimension,
    native GQA, non-empty block mask, and unknown backend all fail before any
    dense SDPA fallback can be used.

- Backend metadata:
  - Evidence:
    `BackendInfo` and `ExtensionStatus` expose backend kind, module path,
    forward/backward readiness, CUDA availability, and current compute
    capability when the extension can report it.
  - Tests:
    `test_backend_info_exposes_forward_only_capability_limits`,
    `test_backend_info_supports_sm8x_fa2_compatible_kind`, and
    `test_extension_status_is_lazy`.
  - Result: complete. Metadata covers SM90 FA3 and SM86/SM8x FA2-compatible
    backend kinds, forward-only readiness, and unavailable states.

- Build modes:
  - Evidence:
    `/home/jake/Developer/flashmask/setup.py`
  - Tests:
    `test_setup_declares_experimental_sm90_kernel_build_surface`,
    `test_setup_normal_metadata_does_not_import_torch`, and
    `test_setup_cuda_build_modes_are_mutually_exclusive`.
  - Result: complete. No-extension metadata remains torch-free; stub,
    experimental SM90, and experimental SM8x build modes are explicit and
    mutually exclusive.

- Runtime dependency boundary:
  - Command:
    `rg -n "import paddle|from paddle|PaddleNLP|paddlenlp" src tests pyproject.toml README.md setup.py -S`
  - Result: no matches.
  - Runtime check: importing `flashmask` loaded no `paddle` or `paddlenlp`
    modules.

- Test results:
  - Focused Phase 2 suite:
    `uv run pytest -q tests/test_backend_contract.py tests/test_package_surface.py tests/test_cuda_extension_optional.py`
  - Result: `50 passed, 12 skipped`.
  - Full CPU-safe suite:
    `uv run pytest -q`
  - Result: `108 passed, 17 skipped`.

## Conclusion

Phase 2 is complete. The stable artifact is the standalone PyTorch extension
ABI and fail-closed backend metadata contract. The raw op surface is locked for
Phase 3 forward kernels and Phase 4 backward without requiring a public API
redesign. Real kernel correctness, speed, router finalization, and PE
integration remain later-phase work.
