# Phase 6 Completion Audit

Date: 2026-05-29

Goal reference:
`/home/jake/Developer/flashmask/goal/phase-6-pe-integration.md`.

## Hardware Policy

Current completion is strict for the available NVIDIA RTX A6000 compute
capability 8.6 GPU. SM90/Hopper PE integration remains a documented,
hard-gated validation path until H100/H200 hardware is available. SM80 runtime
proof remains deferred until SM80/A100-class hardware is available.

## Implemented PE Contract

```text
normal PE backend: flashmask
package request: backend="auto"
local selected backend: fa2-compatible
backend kind: sm8x_sparse_fa2_compatible
device: SM86, compute capability 8.6
forward: verified through PE full/query masks and model logits
backward: verified through PE tiny training
rollout: verified through incremental query masks and sampler KV-cache rollout
```

PE remains a downstream consumer. It imports `flashmask`, compiles PE metadata
to `flashmask.IntervalMask`, and calls the public Python
`flashmask.flashmask_attention` API through `components.attention`.

## Implementation Summary

- `/home/jake/Developer/pe/components/attention.py`
  - Adds the public PE backend name `flashmask`.
  - Maps `flashmask` to package `backend="auto"`.
  - Keeps explicit `flashmask-fa2-compatible` and `flashmask-fa3` aliases for
    proof, diagnostics, and negative routing tests.
  - Rejects dense masks for FlashMask backends and does not fall back to SDPA.
  - Centralizes K/V head expansion before the FlashMask package call.

- `/home/jake/Developer/pe/components/batch.py`
  - Adds full-sequence and query-mask compilers that call FlashMask's PE
    interval-mask builders with PE token ids.
  - Emits `flashmask.IntervalMask` from PE batch construction when a FlashMask
    backend is selected.

- `/home/jake/Developer/pe/model.py`
  - Carries `attention_backend` through block attention calls.
  - Validates incremental FlashMask interval masks against active Q/K lengths.

- `/home/jake/Developer/pe/components/sampler.py`
  - Builds FlashMask query masks from KV-cache metadata during incremental
    rollout.
  - Rebuilds truncated FlashMask masks for logits scoring instead of slicing a
    dense mask.

- `/home/jake/Developer/pe/train.py`
  - Verifies FlashMask training backends before training starts with
    `require_backward=True`.
  - Uses the PE-to-package backend mapping, so normal `flashmask` training
    verifies package `backend="auto"`.

- `/home/jake/Developer/pe/benchmarks/bench_flashmask_attention.py`
  - Records requested/selected backend, backend kind, GPU capability, mask
    density, query shape, and proof outcome fields for Phase 7.

## Verification Evidence

- Focused PE integration tests:
  - Command from `/home/jake/Developer/pe`:
    `PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu pytest -q tests/test_attention.py tests/test_batch.py tests/test_train.py tests/test_flashmask_verification_gates.py`
  - Result: `72 passed in 19.09s`.

- Hard-gated SM8x PE training/parity tests:
  - Command from `/home/jake/Developer/pe`:
    `PYTHONPATH=/home/jake/Developer/flashmask/src PE_REQUIRE_FLASHMASK_SM8X=1 uv run --extra gpu pytest -q tests/test_train.py tests/test_flashmask_sm8x_gpu_parity.py`
  - Result: `35 passed in 4.34s`.

- Broader PE regression suite:
  - Command from `/home/jake/Developer/pe`:
    `PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu pytest -q`
  - Result: `120 passed, 10 skipped in 19.63s`.

- FlashMask package regression suite:
  - Command from `/home/jake/Developer/flashmask`:
    `uv run pytest -q`
  - Result: `117 passed, 26 skipped in 2.57s`.

- Hard-gated SM86 optional FlashMask CUDA tests:
  - Command from `/home/jake/Developer/pe`:
    `FLASHMASK_REQUIRE_SM86=1 uv run --extra gpu pytest -q /home/jake/Developer/flashmask/tests/test_cuda_extension_optional.py`
  - Result: `14 passed, 7 skipped in 3.72s`.

- Hard-gated SM8x optional FlashMask CUDA tests:
  - Command from `/home/jake/Developer/pe`:
    `FLASHMASK_REQUIRE_SM8X=1 uv run --extra gpu pytest -q /home/jake/Developer/flashmask/tests/test_cuda_extension_optional.py`
  - Result: `14 passed, 7 skipped in 3.67s`.

## Requirement Audit

- Editable dependency and workspace import are verified by
  `tests/test_flashmask_verification_gates.py`.
- Full-sequence PE dense-mask semantics match FlashMask reconstruction in
  `tests/test_batch.py`.
- Query/incremental PE dense-mask semantics match FlashMask reconstruction in
  `tests/test_batch.py`.
- Public `flashmask` backend routing to package `auto` is verified in
  `tests/test_attention.py` and `tests/test_train.py`.
- FlashMask backends receive `IntervalMask` objects and reject dense masks in
  `tests/test_attention.py`.
- Dense SDPA fallback is rejected for every FlashMask PE backend in
  `tests/test_attention.py`.
- Training readiness requires backward support in `tests/test_train.py`.
- SM86/SM8x logits and loss parity are verified in
  `tests/test_flashmask_sm8x_gpu_parity.py`.
- SM86/SM8x incremental query-cache parity and sampler KV-cache rollout parity
  are verified in `tests/test_flashmask_sm8x_gpu_parity.py`.
- Incremental FlashMask mask shape errors are verified in `tests/test_model.py`.
- Benchmark artifact readiness is verified by
  `tests/test_flashmask_verification_gates.py`.

## Deferred Work

- SM90/Hopper PE parity and rollout proof remain deferred until H100/H200
  hardware is available and the standalone SM90 backend proof is complete.
- SM80 runtime proof remains deferred until SM80/A100-class hardware is
  available.
- Final production speedup proof and benchmark artifact acceptance remain
  Phase 7.

## Conclusion

Phase 6 is complete for the current hardware policy. PE consumes FlashMask as a
thin downstream dependency, preserves dense state-autoregressive semantics
through full and query interval metadata, routes normal FlashMask usage through
the public `flashmask`/`auto` API, verifies SM86/SM8x forward, backward,
training, logits/loss, and cached rollout parity, and keeps Hopper integration
as a documented hard-gated path.
