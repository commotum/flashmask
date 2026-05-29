# Phase 4: Backward

## Pasteable Goal

Implement FlashMask sparse backward and PyTorch autograd integration for the
current SM86/SM8x backend so PE can train with FlashMask only when that backend
has verified Q/K/V gradient parity. Keep SM90/Hopper backward as a templated,
fail-closed path with hard-gated H100/H200 validation commands. See
`/home/jake/Developer/flashmask/goal/phase-4-backward.md` for the detailed
scope, tests, and exit criteria.

## Objective

Implement backward for the current SM86/SM8x sparse kernel path so PE can train
with FlashMask on the available local backend.

This phase makes the forward kernels completed in Phase 3 usable for training.
Phase 3 completion evidence is recorded in
`/home/jake/Developer/flashmask/goal/phase-3-completion-audit.md`. The backward
path must preserve the same interval-mask semantics and must not route through
dense masked attention as a hidden fallback.

SM90/Hopper backward work in this phase is a template/scaffold only: keep the
source references, build hooks, metadata fields, fail-closed architecture gates,
and hard-gated validation commands ready for later H100/H200 access. SM90
runtime gradient parity and profiler proof are deferred and are not current
Phase 4 exit criteria.

## Phase 3 Handoff

The locally proven Phase 3 runtime path is:

```text
backend: fa2-compatible / sm8x_sparse_fa2_compatible
device: SM86, compute capability 8.6
dtype: fp16, bf16
head_dim dispatch groups: 96, 128
mask: PE non-causal state-autoregressive bound_num=2 interval masks
backward: not implemented, fail-closed
```

Phase 4 should implement backward for this proven SM86 path first. Additional
mask families, SM80 runtime claims, and SM90/Hopper runtime claims should remain
fail-closed until their corresponding forward and backward proof exists.

## Non-Goals

- Do not change PE training policy except to enable FlashMask after backward is
  proven.
- Do not use dense SDPA backward as the FlashMask backend.
- Do not claim backend readiness until Q/K/V gradients are tested.
- Do not add distributed/NVSHMEM overlap unless it is required for single-GPU PE
  training correctness.

## Reference Inputs

Use the reference implementation as source material:

- `sub/Paddle/paddle/phi/kernels/gpu/flash_attn_v3_grad_kernel.cu`
  - FA3-era FlashMask v2 backward wrapper and parameter setup.
- `sub/Paddle/paddle/phi/kernels/gpu/flash_attn_grad_kernel.cu`
  - FA2-era backward wrapper and interval pointer slicing.
- `src/flashmask/csrc/flashmask_v2/flash_bwd_kernel_sm90.h`
  - SM90 backward kernel structure.
- `src/flashmask/csrc/flashmask_v2/flash_api.cu`
  - `flashmaskv2_run_mha_bwd` and backward parameter setters.
- `src/flashmask/csrc/flashmask_v2/flash_prepare_scheduler.cu`
  - backward preprocessing/scheduler setup where applicable.

For each reference component, document whether it is reused, adapted for
PyTorch, removed as framework-specific, or deferred.

## Backward Data Flow

The standalone backward path should have this shape:

```text
PyTorch autograd receives dout
-> retrieve saved q/k/v/out/lse/startend/backend metadata
-> validate backend backward readiness
-> PyTorch extension bwd
-> allocate dq/dk/dv
-> rebuild or reuse sparse preprocessing metadata
-> launch sparse FlashMask backward kernel
-> return dq, dk, dv
```

The backward path must use the same sparse interval semantics as forward.

## Autograd Contract

The Python API should be backed by a custom autograd function.

Forward should save:

- `q`
- `k`
- `v`
- `out`
- `softmax_lse`
- `startend`
- optional `block_mask`
- `softmax_scale`
- `causal`
- selected backend kind
- any backend-specific metadata required for backward

Backward should return gradients for:

- `q`
- `k`
- `v`

Backward should return `None` for:

- mask metadata
- backend selection
- scale and boolean/configuration arguments

If any input requires grad and the selected backend lacks backward support, the
forward call should fail early with a clear error rather than succeeding and
failing later during `.backward()`.

## Gradient Contract

Required gradients:

- `dq`: same shape/device as `q`
- `dk`: same shape/device as `k`
- `dv`: same shape/device as `v`

Required behavior:

- supports FP16 and BF16 forward inputs where the backend supports them
- accumulates gradients with acceptable numerical tolerance
- handles mask-head broadcasting consistently with forward
- handles PE full-sequence masks
- handles PE query/incremental masks if PE training or future fine-tuning uses
  incremental paths
- rejects unsupported native GQA unless explicitly implemented
- rejects unsupported head dimensions or dtypes before launching kernels

## Saved Metadata And Memory

The implementation should be explicit about what is saved from forward versus
recomputed in backward.

Acceptable choices:

- save `softmax_lse` and `out`, recompute sparse block metadata
- save backend-specific preprocessing metadata if doing so is faster and memory
  usage is acceptable

The chosen approach must document:

- extra memory cost
- whether metadata is deterministic
- whether recomputation can diverge from forward
- whether dropout is supported or explicitly unsupported

For PE's immediate needs, dropout should remain unsupported unless the kernel
port already proves it.

## Backend-Specific Requirements

### SM90 / FA3-Compatible Template

The SM90 backward path should be laid out as a Hopper validation template, not a
current completion blocker. It should:

- validate compute capability 9.0
- keep the FA3-compatible sparse backward source/wrapper structure visible
- preserve the Phase 2 raw-op ABI and `startend` interval metadata contract
- expose backend metadata for `sm90_sparse_fa3`
- fail closed on non-Hopper devices or unproven Hopper builds
- document hard-gated H100/H200 commands for later gradient and profiler proof
- keep `backward_ready=False` until those deferred Hopper tests pass

### SM86/SM8x Sparse Path

The SM86/SM8x backward path is the strict Phase 4 implementation target. It
must first cover the locally proven SM86 path:

- validate supported compute capability, with SM86 as the first local target
- use an exact sparse interval backward path
- preserve PE non-causal state-autoregressive `bound_num=2` semantics
- match dense reference gradients
- expose profiler-visible backward kernel markers
- fail closed if only forward is available

SM80/A100 runtime proof is deferred until SM80 hardware is available. The SM8x
build may keep SM80 cubins and metadata, but do not claim SM80 backward runtime
readiness until the hard-gated SM80 tests pass on SM80 hardware.

If any backend remains forward-only, backend routing must report that honestly
and PE training must reject that backend until it is ready.

## C++/CUDA Extension Work

The extension should:

- register `torch.ops.flashmask.bwd`
- allocate `dq`, `dk`, and `dv` using PyTorch tensor factories
- use the current PyTorch CUDA stream
- validate all input shapes, dtypes, devices, and strides
- bind interval pointer metadata consistently with forward
- rebuild or consume `flashmask_maxmin`-equivalent metadata
- return actionable errors for unsupported cases
- expose `backward_ready()` and backend-specific readiness metadata

## Correctness Tests

GPU tests should compare FlashMask gradients against dense reference gradients.

Required cases:

- PE full-sequence mask
- PE query/incremental mask where applicable
- causal mask
- document/sliding-window/prefix-style masks where supported
- mask-head broadcast
- multiple heads
- FP16 and BF16
- head-dim dispatch groups 96 and 128
- representative sequence lengths
- SM86/SM8x backend on the local GPU
- SM80 only as a hard-gated deferred test on SM80 hardware
- SM90/Hopper only as hard-gated deferred tests or template checks; SM90 runtime
  gradient parity is not a current Phase 4 exit criterion

Representative checks:

```python
torch.testing.assert_close(dq_flash, dq_dense, atol=..., rtol=...)
torch.testing.assert_close(dk_flash, dk_dense, atol=..., rtol=...)
torch.testing.assert_close(dv_flash, dv_dense, atol=..., rtol=...)
```

Use deterministic seeds and fixed upstream gradients so failures are
reproducible.

## Gradcheck Strategy

Full numerical `gradcheck` may be too expensive or unsupported for FP16/BF16
kernels. Use layered validation:

- small FP32 dense reference where feasible
- FP16/BF16 gradient parity against dense PyTorch attention
- finite-difference spot checks on tiny shapes if practical
- loss-level parity in PE as an integration test

The tests should distinguish numerical tolerance issues from semantic mask
errors.

## Profiler Evidence

Backward tests should prove the sparse backward path ran.

Required evidence:

- `torch.ops.flashmask.bwd` appears in profiler events
- backend-specific sparse backward kernel marker appears
- dense SDPA/matmul/softmax fallback events do not appear inside the FlashMask
  backward call
- backend kind and `backward_ready=True` are recorded

Profiler marker names may differ by backend, but they must be stable enough for
tests.

## PE Training Gate

PE should continue to reject FlashMask training until this phase is complete for
the selected backend.

After completion:

- `verify_backend(require_backward=True)` must pass for the selected backend
- `train.py --attention-backend flashmask-...` may proceed
- a tiny PE training step should run and produce finite loss/gradients
- dense-vs-FlashMask loss parity should be checked on a representative batch

Inference-only backends may remain available, but PE training must not use them.

## Failure Modes To Test

- forward ready but backward missing
- wrong GPU architecture
- unsupported dtype
- unsupported head dimension
- unsupported native GQA
- invalid `startend` shape/dtype
- mismatched Q/K/V shapes
- non-contiguous inputs if not supported
- CPU tensors passed to CUDA-only backend

All failures should be clear and should not fall back to dense attention.

## Test Commands

CPU-safe tests should still pass:

```bash
uv run pytest -q
```

Optional GPU tests should be hard-gated by backend/build, for example:

```bash
cd /home/jake/Developer/pe
FLASHMASK_REQUIRE_SM8X=1 uv run --extra gpu pytest -q \
  /home/jake/Developer/flashmask/tests/test_cuda_extension_optional.py
FLASHMASK_REQUIRE_SM86=1 uv run --extra gpu pytest -q \
  /home/jake/Developer/flashmask/tests/test_cuda_extension_optional.py
```

Deferred Hopper validation command, recorded now but not required until
H100/H200 hardware is available:

```bash
FLASHMASK_REQUIRE_SM90=1 uv run pytest -q tests/test_cuda_extension_optional.py
```

PE integration tests after backend readiness:

```bash
cd /home/jake/Developer/pe
PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu pytest -q \
  tests/test_train.py tests/test_flashmask_sm8x_gpu_parity.py
```

The exact test files may change, but the final commands must prove gradient and
training readiness.

## Exit Criteria

- SM86/SM8x Q/K/V gradients match dense reference within agreed tolerance on
  the local supported SM86 backend.
- SM86/SM8x backend reports `backward_ready=True` only after SM86 gradient
  tests pass.
- SM80 backward runtime readiness is not claimed until separate SM80 hardware
  proof passes.
- SM90/Hopper backward remains templated and fail-closed unless separate
  H100/H200 gradient and profiler proof has passed; that proof is deferred and
  not required for current Phase 4 completion.
- SM86/SM8x backward profiler evidence proves sparse kernels ran.
- PE training no longer rejects FlashMask backends whose backward is ready.
- A tiny PE training step runs with finite loss and gradients.
- Inference-only backends still fail closed for training.
- No dense SDPA backward fallback is used by the FlashMask backend.
