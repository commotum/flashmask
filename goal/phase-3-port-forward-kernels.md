# Phase 3: Port Forward Kernels

## Pasteable Goal

Port the kernel-native sparse forward paths into the standalone PyTorch
extension while preserving the Phase 2 ABI: prove SM86/SM8x dense-reference
parity and sparse-kernel execution on the available local GPU, and put the SM90
FA3-compatible path in place with build/fail-closed/hard-gated verification
hooks for later Hopper runtime proof. See
`/home/jake/Developer/flashmask/goal/phase-3-port-forward-kernels.md` for the
detailed scope, tests, and exit criteria.

## Objective

Port/adapt the kernel-native sparse forward paths into the standalone PyTorch
extension without requiring Hopper hardware for the current phase.

This phase turns the Phase 2 ABI into real sparse attention execution on the
available SM86/SM8x target. The central runtime requirement for the local phase
is that disallowed Q/K interactions are handled by the sparse kernel path, not
by dense SDPA masking. SM90/Hopper runtime parity, profiler, and speed proof are
deferred until Hopper hardware is available, but the SM90 template path must
remain wired, buildable where practical, fail-closed on non-Hopper devices, and
covered by a reproducible hard-gated test command.

No current Phase 3 exit criterion may depend on executing code on an SM90/Hopper
GPU. Hopper access is a later validation pass, not a blocker for completing the
local Phase 3 work.

## Phase 2 Handoff

Phase 2 locked the public extension ABI. Phase 3 must build on it, not redesign
it casually.

The raw torch ops are:

```text
torch.ops.flashmask.fwd(q, k, v, startend, block_mask, softmax_scale, causal)
torch.ops.flashmask.bwd(dout, q, k, v, out, softmax_lse, startend, block_mask,
                        softmax_scale, causal, deterministic)
```

The raw ops do not take a backend string. The Python layer maps public backend
names to extension backend kinds and validates the loaded extension before raw
op dispatch:

- `"fa3"` -> `sm90_sparse_fa3`
- `"fa2-compatible"` -> `sm8x_sparse_fa2_compatible`

Optional `block_mask` at the Python API boundary is represented as an empty
int32 tensor at the raw op boundary until block-mask kernels are implemented.

Extension metadata must continue to expose:

- backend kind
- module path
- CUDA availability
- current compute capability
- forward readiness
- backward readiness, which remains false in this phase

Phase 2 is complete. The Phase 2 completion evidence is
`/home/jake/Developer/flashmask/goal/phase-2-completion-audit.md`.
Phase 3 should treat this ABI as fixed unless a required correction is
documented with updated tests and a replacement audit note.

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
-> Python validates requested backend against extension metadata
-> PyTorch extension fwd with Phase 2 raw-op ABI
-> validate layout/dtype/device/readiness
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

## SM90 / FA3-Compatible Template Path

The SM90 path should lay out the FlashMask v2/FA3-compatible implementation as
a Hopper template path. It is not a current runtime proof target.

Current Phase 3 requirements that do not require Hopper hardware:

- SM90 source path is present in the standalone extension tree.
- SM90 build mode is explicit and separate from stub and SM8x build modes.
- compute capability 9.0 validation fails closed on non-Hopper devices.
- backend metadata identifies the SM90 build as `sm90_sparse_fa3`.
- raw op ABI and Python backend selection match the Phase 2 contract.
- FP16 and BF16 forward instantiations are present for the documented head
  dimensions.
- `startend` with `bound_num` in `{1, 2, 4}` is accepted by the wrapper where
  the underlying reference design supports it.
- no dense SDPA or dense attention-mask fallback is introduced.
- a hard-gated Hopper verification command is documented so that later H100/H200
  access can produce runtime proof without redesigning this phase.

Deferred Hopper verification, not required for current Phase 3 completion:

- SM90 forward output and LSE parity against dense reference.
- SM90 profiler markers for FlashMask preprocessing and forward kernels.
- SM90 speed sanity or benchmark proof.

Known Paddle-specific pieces to replace:

- Paddle `DenseTensor`
- Paddle slicing helpers
- Paddle allocator
- Paddle op registry
- Paddle dynload indirection
- Paddle distributed/NVSHMEM setup unless explicitly deferred

The standalone implementation may use static C++/CUDA calls rather than an
opaque dynload ABI if that is simpler and keeps the package small. Runtime SM90
claims must not be made until the deferred Hopper verification command passes on
Hopper hardware.

## SM86 / SM8x Sparse Path

The SM86/SM8x path must be exact for the same interval semantics. Stock FA2
causal/window/padding masks are not enough. The vendored sources may use SM80
mainloop names internally, but the current Phase 2 build/readiness gate is the
SM8x FA2-compatible backend with SM86 as the first local target. Any additional
SM80 support must have explicit build flags, backend metadata, parity tests, and
profiler evidence before being claimed.

Acceptable implementation strategies:

- adapt the copied FlashMask v2 SM80/SM86-family forward sources
- implement a custom sparse interval mainloop compatible with the Phase 2 ABI
- reuse FA2-compatible building blocks only when fully masked tiles are skipped
  in-kernel and PE interval semantics are preserved

Required capabilities:

- compute capability 8.6 validation for the first supported local target
- explicit metadata and tests for any additional SM8x compute capability
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
- expose backend kind, CUDA availability, current compute capability, and
  readiness metadata
- register `torch.ops.flashmask.fwd`
- keep `torch.ops.flashmask.bwd` registered and fail-closed until Phase 4
- preserve the Phase 2 ABI unless a documented ABI correction is required

The Python wrapper should:

- move/validate `IntervalMask` metadata
- expand K/V for unsupported native GQA only when explicitly designed and tested
- keep the selected backend visible in returned/profiler metadata
- avoid dense SDPA fallback

## Correctness Tests

GPU forward tests should compare against a dense reference for the available
SM86/SM8x backend:

- output tensor
- softmax LSE where applicable
- multiple sequence lengths
- multiple batch sizes
- multiple heads
- mask-head broadcast
- FP16 and BF16
- SM86/SM8x backend on the local GPU

SM90 dense-reference tests should exist as hard-gated optional tests, but they
are not current Phase 3 exit criteria until Hopper hardware is available.

The dense reference may compute attention densely for testing, but it must be
outside the FlashMask fast path.

Representative checks:

- `torch.testing.assert_close(out, dense_out, atol=..., rtol=...)`
- `torch.testing.assert_close(lse, dense_lse, atol=..., rtol=...)`
- no dense attention profiler events inside the FlashMask call

## Profiler And Kernel Evidence

Tests and benchmark records should prove the local SM86/SM8x sparse path ran.

Required local evidence:

- `torch.ops.flashmask.fwd` appears in profiler events
- preprocessing kernel marker appears when preprocessing is CUDA-side
- sparse FlashMask forward kernel marker appears
- dense SDPA, dense matmul/softmax fallback markers do not appear inside the
  FlashMask call
- backend kind is recorded as `sm8x_sparse_fa2_compatible`
- CUDA availability and current compute capability are recorded in backend
  metadata

SM90 profiler evidence is deferred until Hopper hardware is available. Profiler
marker names may differ by implementation, but they must be stable enough for
tests on each backend once that backend can be run.

## Performance Sanity

This phase does not need final PE benchmark proof, but it should include kernel
sanity measurements to catch obviously wrong ports.

Minimum sanity checks:

- sparse path is not catastrophically slower than dense reference on highly
  sparse masks
- dense-equivalent masks are allowed to be similar speed
- fully masked tile count correlates with lower kernel work
- SM86/SM8x records report backend-specific timing

SM90 timing records are deferred until Hopper hardware is available.

Final material speedup proof belongs to Phase 7.

## Test Commands

CPU-safe tests should still pass:

```bash
uv run pytest -q
```

Optional GPU tests should be hard-gated by architecture/build. The hard gates
are already split by backend:

- `FLASHMASK_REQUIRE_SM90=1` requires a ready SM90 `sm90_sparse_fa3`
  extension and must fail instead of skip when that backend is unavailable.
- `FLASHMASK_REQUIRE_SM8X=1` requires a ready SM80 or SM86
  `sm8x_sparse_fa2_compatible` extension and must fail instead of skip when
  that backend is unavailable.

Local SM8x examples:

```bash
FLASHMASK_BUILD_EXPERIMENTAL_SM8X_CUDA=1 CUTLASS_HOME=/path/to/cutlass \
  uv pip install -e . --no-build-isolation -v
FLASHMASK_REQUIRE_SM8X=1 uv run pytest -q tests/test_cuda_extension_optional.py
```

Use `FLASHMASK_REQUIRE_SM86=1` to require exact SM86 runtime proof on the local
RTX A6000-class path. Use `FLASHMASK_REQUIRE_SM80=1` on SM80/A100 hardware
before claiming SM80 runtime parity/profiler proof. The SM8x build may include
both SM80 and SM86 cubins, but runtime proof is architecture-specific.

When using PE's CUDA-enabled uv environment as the local GPU test environment,
install FlashMask into that interpreter and run the FlashMask optional tests by
absolute path:

```bash
FLASHMASK_BUILD_EXPERIMENTAL_SM8X_CUDA=1 CUTLASS_HOME=/path/to/cutlass \
  uv pip install --python /home/jake/Developer/pe/.venv/bin/python \
  -e /home/jake/Developer/flashmask --no-build-isolation -v

cd /home/jake/Developer/pe
FLASHMASK_REQUIRE_SM8X=1 uv run --extra gpu pytest -q \
  /home/jake/Developer/flashmask/tests/test_cuda_extension_optional.py
```

The exact commands can change with the final test layout, but Phase 3 must
leave reproducible commands for every supported backend.

Deferred Hopper verification command, recorded now but not required until
Hopper hardware is available:

```bash
FLASHMASK_BUILD_EXPERIMENTAL_CUDA=1 CUTLASS_HOME=/path/to/cutlass \
  uv pip install -e . --no-build-isolation -v
FLASHMASK_REQUIRE_SM90=1 uv run pytest -q tests/test_cuda_extension_optional.py
```

## Exit Criteria

- SM86/SM8x forward output and LSE match dense reference on GPU within agreed
  tolerance for the supported sparse interval path.
- Tests or profiler evidence prove the local SM86/SM8x sparse kernel path runs
  and dense SDPA/matmul/softmax fallback does not run inside the FlashMask call.
- SM90 artifacts identify the FA3-compatible backend when built, validate
  compute capability 9.0, and fail closed on non-Hopper devices.
- SM90 hard-gated runtime parity/profiler tests are present and documented for
  later Hopper verification, but they are not required to pass in the current
  non-Hopper environment.
- SM86/SM8x artifacts identify the exact sparse interval backend and current
  compute capability.
- Unsupported masks/backends fail closed.
- No dense SDPA attention-mask fallback is used by the FlashMask fast path.
- Phase 4 can implement backward without redesigning the Phase 2/3 forward ABI.

## Deferred Hopper Verification

When Hopper hardware is available, run the hard-gated SM90 command above and
record a proof artifact showing:

- SM90 forward output and LSE match dense reference on GPU within agreed
  tolerance.
- SM90 FlashMask preprocessing and sparse forward kernel profiler markers are
  present.
- dense SDPA, dense matmul, and dense softmax fallback markers are absent inside
  the FlashMask call.
- backend metadata reports `sm90_sparse_fa3` on compute capability 9.0.
