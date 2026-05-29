# Phase 4 Completion Audit

Date: 2026-05-29

Goal reference:
`/home/jake/Developer/flashmask/goal/phase-4-backward.md`.

## Hardware Policy

Current completion is strict for the available NVIDIA RTX A6000 compute
capability 8.6 GPU. SM90/Hopper runtime gradient parity, profiler proof, and
training proof remain deferred until H100/H200 hardware is available. SM80
runtime proof remains deferred until SM80/A100-class hardware is available.

## Implemented Runtime Scope

```text
backend: fa2-compatible / sm8x_sparse_fa2_compatible
device: SM86, compute capability 8.6
dtype: fp16, bf16
head_dim dispatch groups: 96, 128
mask: PE non-causal state-autoregressive bound_num=2 interval masks
backward: implemented and verified for SM86
```

The SM8x extension now reports `backward_ready=True` only for the SM8x
FA2-compatible build. The SM90/FA3-compatible build keeps
`backward_ready=False` and the runtime backward path remains fail-closed until
Hopper proof exists.

## Implementation Summary

- `src/flashmask/csrc/flashmask_experimental.cu`
  - Adds backward input validation for CUDA device, dtype, contiguity, shape,
    `softmax_lse` layout, and deterministic-mode rejection.
  - Adds a profiler-visible `flashmask_sm8x_backward` CUDA kernel for SM80/SM86
    builds.
  - Recomputes sparse probabilities from saved `q`, `k`, `out`, and
    `softmax_lse`, then accumulates `dq`, `dk`, and `dv` in float buffers before
    casting back to the input dtype.
  - Uses the same `startend` interval semantics as the SM8x forward path for
    PE's non-causal `bound_num=2` masks.

- `setup.py`
  - Sets `FLASHMASK_BACKWARD_READY=1` for the experimental SM8x build.
  - Leaves stub and SM90 experimental builds backward-not-ready.

- `tests/test_cuda_extension_optional.py`
  - Adds raw-op Q/K/V gradient parity against a dense PyTorch reference.
  - Adds public `flashmask.flashmask_attention(...)` autograd parity.
  - Adds profiler checks requiring `flashmask::bwd` and
    `flashmask_sm8x_backward`, while rejecting dense SDPA/matmul/softmax
    fallback events inside the FlashMask backward call.

- `/home/jake/Developer/pe/tests/test_flashmask_sm8x_gpu_parity.py`
  - Requires `verify_backend(..., require_backward=True)` for SM8x PE GPU
    tests.
  - Adds a tiny FlashMask GPT training step with finite loss, finite gradients,
    and an optimizer step.

## Saved Metadata And Memory

The Python autograd path saves `q`, `k`, `v`, `out`, `softmax_lse`,
`startend`, `block_mask`, scalar scale/causal state, and backend metadata.
Backward does not save forward scheduler scratch; it recomputes pairwise sparse
probabilities from saved tensors and the interval metadata.

Additional memory during backward is three float accumulation tensors matching
`q`, `k`, and `v`. Deterministic backward and dropout remain unsupported. The
current path therefore cannot diverge from forward due to metadata
reconstruction, but the float atomic accumulation order is not deterministic.

## Verification Evidence

- Extension build in PE with corrected CUTLASS path:
  - Command:
    `FLASHMASK_BUILD_EXPERIMENTAL_SM8X_CUDA=1 CUTLASS_HOME=/home/jake/Developer/MonSTERs/flash-attention/csrc/cutlass uv pip install --python /home/jake/Developer/pe/.venv/bin/python -e /home/jake/Developer/flashmask --no-build-isolation -v`
  - Result: succeeded.

- Runtime metadata in PE:
  - Command:
    `uv run --extra gpu python - <<'PY' ...`
  - Result: backend kind `sm8x_sparse_fa2_compatible`, compute capability
    `(8, 6)`, supported capabilities `((8, 0), (8, 6))`,
    `forward_ready=True`, `backward_ready=True`, and
    `training_available=True`.

- SM8x hard-gated FlashMask CUDA tests:
  - Command from `/home/jake/Developer/pe`:
    `FLASHMASK_REQUIRE_SM8X=1 uv run --extra gpu pytest -q /home/jake/Developer/flashmask/tests/test_cuda_extension_optional.py`
  - Result: `13 passed, 7 skipped`.

- SM86 hard-gated FlashMask CUDA tests:
  - Command from `/home/jake/Developer/pe`:
    `FLASHMASK_REQUIRE_SM86=1 uv run --extra gpu pytest -q /home/jake/Developer/flashmask/tests/test_cuda_extension_optional.py`
  - Result: `13 passed, 7 skipped`.

- FlashMask CPU-safe regression suite:
  - Command from `/home/jake/Developer/flashmask`:
    `uv run pytest -q`
  - Result: `112 passed, 25 skipped`.

- PE Phase 4 training/parity command:
  - Command from `/home/jake/Developer/pe`:
    `PYTHONPATH=/home/jake/Developer/flashmask/src PE_REQUIRE_FLASHMASK_SM8X=1 uv run --extra gpu pytest -q tests/test_train.py tests/test_flashmask_sm8x_gpu_parity.py`
  - Result: `31 passed`.

- Broader PE regression suite:
  - Command from `/home/jake/Developer/pe`:
    `PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu pytest -q`
  - Result: `109 passed, 10 skipped`.

## Deferred Work

- SM80 runtime backward readiness requires separate SM80/A100 proof.
- SM90/Hopper backward readiness requires separate H100/H200 gradient and
  profiler proof.
- Causal and `bound_num=4` sparse backward masks remain outside the current
  SM86 completion claim unless later phases explicitly add and prove them.
- Final speedup/proof artifacts remain Phase 7.

## Conclusion

Phase 4 is complete for the current hardware policy. The SM86/SM8x
FA2-compatible backend has native sparse backward, Q/K/V gradient parity,
profiler evidence, public autograd coverage, and PE training smoke coverage.
Forward-only and unproven backends still fail closed for training.
