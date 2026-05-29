# Phase 3: Port Forward Kernels

## Pasteable Goal

Port the kernel-native sparse forward paths into the standalone PyTorch
extension for SM90 FA3-compatible and SM80/SM86 exact interval-mask backends,
proving dense-reference parity and real sparse-kernel execution. See
`/home/jake/Developer/flashmask/goal/phase-3-port-forward-kernels.md` for the
detailed scope, tests, and exit criteria.

## Objective

Port/adapt the kernel-native sparse forward paths into the standalone PyTorch
extension.

This phase turns the Phase 2 ABI into real sparse attention execution. The
central requirement is that disallowed Q/K interactions are skipped by the
kernel when an entire tile is masked, not computed densely and masked later.

## Non-Goals

- Do not implement backward in this phase.
- Do not claim PE training support.
- Do not use dense SDPA masking as the FlashMask fast path.
- Do not add PE benchmark proof gates before forward parity and profiler checks
  are reliable.

## Reference Inputs

Use the reference implementation as source material, not as a runtime
dependency.

Primary reference files:

- `sub/Paddle/paddle/phi/kernels/gpu/flash_attn_v3_kernel.cu`
  - FA3 FlashMask v2 forward wrapper and parameter setup.
- `sub/Paddle/paddle/phi/kernels/gpu/flash_attn_v3_utils.cu`
  - parameter-handle helpers and setup patterns.
- `sub/Paddle/paddle/phi/backends/dynload/flashmaskv2.h`
  - FA3-style FlashMask v2 ABI shape.
- `sub/Paddle/paddle/phi/kernels/gpu/flash_attn_kernel.cu`
  - FA2-era forward wrapper and interval pointer slicing.
- `src/flashmask/csrc/flashmask_v2/`
  - local copied/adapted FlashMask v2 kernel sources.
- `context/generate_startend_row_indices.py`
  - dense semantics for output/LSE parity checks.

For every copied/adapted component, record whether it is:

- directly reused
- modified for PyTorch tensors/streams/allocation
- removed because it is Paddle-specific
- deferred to backward or distributed support

## Forward Data Flow

The standalone forward path should have this shape:

```text
Python IntervalMask
-> startend int32 tensor
-> PyTorch extension fwd
-> validate layout/dtype/backend
-> slice interval pointers or bind equivalent views
-> allocate/precompute flashmask_maxmin metadata
-> launch sparse FlashMask kernel
-> return out and softmax_lse
```

The forward path must not accept a dense boolean mask as the fast-path mask.

## Preprocessing Contract

The forward kernel requires block-level metadata derived from token-level
intervals.

The preprocessing step must:

- read lower/upper interval vectors from `startend`
- compute per-K-block minima/maxima used by the kernel
- support the block shapes required by each backend
- produce metadata equivalent to the reference `flashmask_maxmin`
- run on CUDA when the sparse kernel path is used
- expose profiler-visible markers so tests can confirm preprocessing occurred

The output should let the main attention kernel classify each Q/K tile as:

- fully masked: skip score computation
- fully visible: run attention without token-mask application
- partially masked: compute tile and apply token-level interval mask

## SM90 / FA3-Compatible Path

The SM90 path should port the FlashMask v2/FA3-compatible implementation.

Required capabilities:

- compute capability 9.0 validation
- FP16 and BF16 forward
- head dimension initially up to 128 unless more is proven
- `startend` with `bound_num` in `{1, 2, 4}`
- PE non-causal interval masks
- causal masks where supported by the reference design
- output tensor and LSE parity against dense reference
- profiler markers for FlashMask preprocessing and forward kernel

Known Paddle-specific pieces to replace:

- Paddle `DenseTensor`
- Paddle slicing helpers
- Paddle allocator
- Paddle op registry
- Paddle dynload indirection
- Paddle distributed/NVSHMEM setup unless explicitly deferred

The standalone implementation may use static C++/CUDA calls rather than an
opaque dynload ABI if that is simpler and keeps the package small.

## SM80/SM86 Sparse Path

The SM80/SM86 path must be exact for the same interval semantics. Stock FA2
causal/window/padding masks are not enough.

Acceptable implementation strategies:

- adapt the copied FlashMask v2 SM80/SM86 forward sources
- implement a custom sparse interval mainloop compatible with the Phase 2 ABI
- reuse FA2-compatible building blocks only when fully masked tiles are skipped
  in-kernel and PE interval semantics are preserved

Required capabilities:

- compute capability 8.0/8.6 validation, with SM86 as the first local target
- FP16 and BF16 forward where practical
- PE non-causal state-autoregressive interval masks
- output and LSE parity against dense reference
- profiler evidence that the FlashMask sparse kernel ran
- no route through dense SDPA attention masks

Partial-block tiles may still compute scores and apply token-level masking.
Fully masked tiles must be skipped by the kernel scheduler/mainloop.

## Mask Semantics To Preserve

Forward parity must cover:

- full-sequence PE masks
- cached rollout query masks
- same-timestep state visibility
- special-token visibility
- padding exclusion
- mask-head broadcasting
- causal masks
- document masks
- sliding-window masks
- prefix-LM masks
- blockwise/global-token/QK-sparse masks where representable

If a backend supports a subset initially, unsupported masks must fail clearly
rather than silently using a dense fallback.

## PyTorch Integration Details

The C++/CUDA extension should:

- use the current PyTorch CUDA stream
- allocate outputs with PyTorch tensor factories
- validate tensor devices and dtypes before launching kernels
- keep error messages actionable
- expose backend kind and readiness metadata
- register `torch.ops.flashmask.fwd`
- preserve the Phase 2 ABI unless a documented ABI correction is required

The Python wrapper should:

- move/validate `IntervalMask` metadata
- expand K/V for unsupported native GQA only when explicitly designed and tested
- keep the selected backend visible in returned/profiler metadata
- avoid dense SDPA fallback

## Correctness Tests

GPU forward tests should compare against a dense reference for:

- output tensor
- softmax LSE where applicable
- multiple sequence lengths
- multiple batch sizes
- multiple heads
- mask-head broadcast
- FP16 and BF16
- SM90 backend when available
- SM80/SM86 backend when available

The dense reference may compute attention densely for testing, but it must be
outside the FlashMask fast path.

Representative checks:

- `torch.testing.assert_close(out, dense_out, atol=..., rtol=...)`
- `torch.testing.assert_close(lse, dense_lse, atol=..., rtol=...)`
- no dense attention profiler events inside the FlashMask call

## Profiler And Kernel Evidence

Tests and benchmark records should prove the sparse path ran.

Required evidence:

- `torch.ops.flashmask.fwd` appears in profiler events
- preprocessing kernel marker appears when preprocessing is CUDA-side
- sparse FlashMask forward kernel marker appears
- dense SDPA, dense matmul/softmax fallback markers do not appear inside the
  FlashMask call
- backend kind is recorded as `sm90_sparse_fa3` or
  `sm8x_sparse_fa2_compatible`

Profiler marker names may differ by implementation, but they must be stable
enough for tests.

## Performance Sanity

This phase does not need final PE benchmark proof, but it should include kernel
sanity measurements to catch obviously wrong ports.

Minimum sanity checks:

- sparse path is not catastrophically slower than dense reference on highly
  sparse masks
- dense-equivalent masks are allowed to be similar speed
- fully masked tile count correlates with lower kernel work
- SM86 and SM90 records report backend-specific timing separately

Final material speedup proof belongs to Phase 7.

## Test Commands

CPU-safe tests should still pass:

```bash
uv run pytest -q
```

Optional GPU tests should be hard-gated by architecture/build, for example:

```bash
FLASHMASK_REQUIRE_SM90=1 uv run pytest -q tests/test_cuda_extension_optional.py
FLASHMASK_REQUIRE_SM8X=1 uv run pytest -q tests/test_cuda_extension_optional.py
```

The exact commands can change with the final test layout, but Phase 3 must
leave reproducible commands for each supported backend.

## Exit Criteria

- SM90 forward output and LSE match dense reference on GPU within agreed
  tolerance.
- SM80/SM86 forward output and LSE match dense reference on GPU within agreed
  tolerance for the supported sparse interval path.
- Tests or profiler evidence prove fully masked tiles are skipped by the sparse
  kernel path.
- SM90 artifacts identify the FA3-compatible backend.
- SM80/SM86 artifacts identify the exact sparse interval backend.
- Unsupported masks/backends fail closed.
- No dense SDPA attention-mask fallback is used by the FlashMask fast path.
- Phase 4 can implement backward without redesigning the forward ABI.
