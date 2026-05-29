# Phase 3 Completion Audit

Date: 2026-05-29

Goal reference:
`/home/jake/Developer/flashmask/goal/phase-3-port-forward-kernels.md`.

## Hardware Policy

Current completion is strict for the available NVIDIA RTX A6000 compute
capability 8.6 GPU. SM90/Hopper runtime parity, profiler evidence, and speed
proof are deferred until H100/H200 hardware is available. The SM90 path is
required to remain templated, build-gated, metadata-visible, and fail-closed.

## Reference Component Mapping

- `sub/Paddle/paddle/phi/kernels/gpu/flash_attn_v3_kernel.cu`
  - Conceptual/reference source for the FA3-compatible FlashMask v2 forward
    wrapper and parameter setup.
  - Adapted into the standalone PyTorch extension through
    `src/flashmask/csrc/flashmask_experimental.cu` and the vendored
    `src/flashmask/csrc/flashmask_v2/` sources.
  - Paddle `DenseTensor`, allocator, slicing helpers, op registry, and dynload
    pieces are not runtime dependencies.

- `sub/Paddle/paddle/phi/kernels/gpu/flash_attn_v3_utils.cu`
  - Conceptual/reference source for parameter setup patterns.
  - Replaced by PyTorch tensor validation, allocation, stream handling, and
    `Flash_fwd_params` population in `flashmask_experimental.cu`.

- `sub/Paddle/paddle/phi/backends/dynload/flashmaskv2.h`
  - Conceptual ABI reference only.
  - Replaced by static C++/CUDA calls registered under
    `torch.ops.flashmask.fwd/bwd`.

- `sub/Paddle/paddle/phi/kernels/gpu/flash_attn_kernel.cu`
  - Conceptual FA2-era reference for interval pointer slicing and
    `flashmask_maxmin` handoff.
  - Adapted for SM8x through `set_startend_ptrs(...)`,
    FlashMask max/min preprocessing, and SM80/86 mainloop integration.

- `src/flashmask/csrc/flashmask_v2/`
  - Vendored/adapted FlashMask v2 CUDA source used by the standalone build.
  - Forward SM80/SM86 and SM90 instantiation files are present for FP16/BF16 and
    head-dim dispatch groups 96/128.
  - Backward sources are retained for Phase 4 but remain fail-closed in Phase 3.

- `context/generate_startend_row_indices.py`
  - Dense semantic reference only.
  - Tests use FlashMask's Python dense reconstruction helpers as the parity
    oracle; no reference-framework runtime import is used.

## Proven Requirements

- Phase 2 ABI is preserved.
  - Raw ops remain:
    `torch.ops.flashmask.fwd(q, k, v, startend, block_mask, softmax_scale, causal)`
    and
    `torch.ops.flashmask.bwd(dout, q, k, v, out, softmax_lse, startend,
    block_mask, softmax_scale, causal, deterministic)`.
  - Verified by `tests/test_backend_contract.py` and
    `tests/test_package_surface.py`.

- SM86/SM8x backend metadata is correct in the PE CUDA environment.
  - Command:
    `uv run --extra gpu python - <<'PY' ...`
  - Result:
    backend kind `sm8x_sparse_fa2_compatible`, compute capability `(8, 6)`,
    supported capabilities `((8, 0), (8, 6))`, `forward_ready=True`, and
    `backward_ready=False`.

- SM86/SM8x forward parity and sparse-kernel execution are proven on the local
  GPU.
  - Command from `/home/jake/Developer/pe`:
    `FLASHMASK_REQUIRE_SM8X=1 uv run --extra gpu pytest -q /home/jake/Developer/flashmask/tests/test_cuda_extension_optional.py`
  - Result: `10 passed, 7 skipped`.
  - Covered cases include PE full-sequence masks, cached/query masks,
    multi-batch inputs, multiple heads, mask-head broadcasting, FP16, BF16,
    head-dim dispatch groups 96/128, output parity, and LSE parity.
  - Profiler checks require `flashmask::fwd`, `scanMaxMinChunkedKernel`, and
    `cutlass_flashmask_kernel`, and reject dense SDPA/matmul/softmax fallback
    events inside the FlashMask call.

- Exact SM86 hard gate passes on the local RTX A6000.
  - Command from `/home/jake/Developer/pe`:
    `FLASHMASK_REQUIRE_SM86=1 uv run --extra gpu pytest -q /home/jake/Developer/flashmask/tests/test_cuda_extension_optional.py`
  - Result: `10 passed, 7 skipped`.

- CPU-safe regression suite passes.
  - Command from `/home/jake/Developer/flashmask`:
    `uv run pytest -q`
  - Result: `112 passed, 22 skipped`.

- Focused source/API/proof tests pass.
  - Command from `/home/jake/Developer/flashmask`:
    `uv run pytest -q tests/test_proof.py tests/test_package_surface.py tests/test_backend_contract.py`
  - Result: `78 passed`.

- Unsupported local masks/backends fail closed.
  - SM8x accepts the current PE non-causal `bound_num=2` interval path.
  - Causal masks and `bound_num=4` masks are rejected by the SM8x optional
    tests instead of falling back to dense attention.
  - Backward remains registered and fail-closed.
  - Explicit `backend='fa3'` fails on the SM8x build with:
    `loaded backend kind 'sm8x_sparse_fa2_compatible' does not match requested
    backend 'fa3'`.

- SM90/Hopper template requirements are in place for current Phase 3 scope.
  - SM90 source and FP16/BF16 head-dim 96/128 instantiations are present under
    `src/flashmask/csrc/flashmask_v2/instantiations/`.
  - `setup.py` defines an explicit SM90 build mode separate from stub and SM8x
    build modes.
  - Extension metadata reports `sm90_sparse_fa3` for SM90 builds and compute
    capability 9.0 support through the same Phase 2 ABI.
  - Hard-gated SM90/Hopper validation commands are documented in the Phase 3
    goal file and remain deferred until Hopper hardware is available.

## Current Supported Runtime Scope

```text
backend: fa2-compatible / sm8x_sparse_fa2_compatible
device: SM86, compute capability 8.6
dtype: fp16, bf16
head_dim dispatch groups: 96, 128
mask: PE non-causal state-autoregressive bound_num=2 interval masks
backward: not implemented, fail-closed
```

## Deferred Work

- SM80 runtime parity/profiler proof requires SM80/A100-class hardware.
- SM90/Hopper runtime parity/profiler proof requires H100/H200-class hardware.
- Backward and training support remain Phase 4.
- Final PE speedup proof remains Phase 7.

## Conclusion

Phase 3 is complete for the current hardware policy: SM86/SM8x forward is
kernel-native, parity-tested, profiler-proven, and fail-closed for unsupported
cases. SM90/Hopper is laid out as a documented, fail-closed template path for
later hard-gated validation.
