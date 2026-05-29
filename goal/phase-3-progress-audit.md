# Phase 3 Progress Audit

Date: 2026-05-29

Goal reference:
`/home/jake/Developer/flashmask/goal/phase-3-port-forward-kernels.md`.

## Proven Locally

- Phase 2 ABI is preserved.
  - Raw ops remain:
    `torch.ops.flashmask.fwd(q, k, v, startend, block_mask, softmax_scale, causal)`
    and
    `torch.ops.flashmask.bwd(dout, q, k, v, out, softmax_lse, startend,
    block_mask, softmax_scale, causal, deterministic)`.
  - Python backend mapping remains `"fa3" -> sm90_sparse_fa3` and
    `"fa2-compatible" -> sm8x_sparse_fa2_compatible`.

- SM86/SM8x runtime forward parity is proven on the local RTX A6000
  compute capability 8.6 GPU for the currently supported sparse interval path.
  - Covered by:
    `/home/jake/Developer/flashmask/tests/test_cuda_extension_optional.py`
  - Verified command:
    `FLASHMASK_REQUIRE_SM8X=1 uv run --extra gpu pytest -q /home/jake/Developer/flashmask/tests/test_cuda_extension_optional.py`
    from `/home/jake/Developer/pe`.
  - Result: `10 passed, 7 skipped`.
  - Covered cases include PE full-sequence masks, cached/query masks,
    multi-batch inputs, multiple heads, mask-head broadcasting, FP16, BF16,
    head dimensions routed through the 96 and 128 dispatch groups, output
    parity, and LSE parity.

- SM86/SM8x sparse-kernel execution is proven on the local GPU.
  - Profiler evidence checks for `flashmask::fwd`,
    `scanMaxMinChunkedKernel`, and `cutlass_flashmask_kernel`.
  - The same test asserts dense SDPA/matmul/softmax fallback events are absent
    inside the FlashMask call.

- SM86/SM8x performance sanity is covered on the local GPU.
  - A synthetic interval mask with most K blocks fully masked runs through the
    same raw FlashMask op and is checked against both a dense-equivalent
    interval call and a dense reference implementation.
  - This is intentionally a loose sanity check, not the final Phase 7 speedup
    gate.

- SM8x fail-closed behavior is covered for unsupported local forward masks.
  - Current SM8x V2 path accepts PE-style non-causal `bound_num=2` interval
    masks.
  - Causal masks and `bound_num=4` masks fail clearly instead of routing to a
    dense fallback.
  - Backward remains registered and fail-closed.

- SM80 build support is mechanically in place.
  - The SM8x extension build includes both `sm_80` and `sm_86` gencodes.
  - Explicit SM80 instantiation files exist for FP16/BF16 and head dimensions
    96/128.
  - The wrapper dispatches SM80 and SM86 through architecture-specific
    instantiations.
  - Extension metadata reports supported capabilities `((8, 0), (8, 6))`.

- SM80 proof validation is first-class.
  - `flashmask.proof` accepts `--backend sm80`.
  - `validate_sm80_proof_jsonl(...)` and `validate_sm80_proof_records(...)`
    validate SM80 proof artifacts with capability `[8, 0]`.

- CPU-safe regression tests pass.
  - Command: `uv run pytest -q`
  - Result: `112 passed, 22 skipped`.

- Focused source/API/proof tests pass.
  - Command:
    `uv run pytest -q tests/test_proof.py tests/test_package_surface.py tests/test_backend_contract.py`
  - Result: `78 passed`.

## Not Yet Proven

- SM80 runtime parity and profiler proof are not proven locally.
  - Later verification hardware: SM80/A100-class GPU.
  - Intended gate:
    `FLASHMASK_REQUIRE_SM80=1 uv run pytest -q tests/test_cuda_extension_optional.py`
    after installing an SM8x experimental build.

- SM90/Hopper runtime parity and profiler proof are deferred by the Phase 3
  plan.
  - Later verification hardware: SM90/H100 or H200-class GPU.
  - Intended gate:
    `FLASHMASK_REQUIRE_SM90=1 uv run pytest -q tests/test_cuda_extension_optional.py`
    after installing the SM90 experimental build.

- Final speedup proof is not part of current Phase 3 completion.
  - Phase 3 only requires kernel sanity and no dense fallback.
  - Material benchmark proof remains Phase 7.

## Current Supported Runtime Scope

The locally proven runtime path is:

```text
backend: fa2-compatible / sm8x_sparse_fa2_compatible
device: SM86, compute capability 8.6
dtype: fp16, bf16
head_dim dispatch groups: 96, 128
mask: PE non-causal state-autoregressive bound_num=2 interval masks
backward: not implemented, fail-closed
```

Unsupported masks or backend requests must continue to fail closed until their
kernel paths are implemented and independently proven.
