# Phase 5 Completion Audit

Date: 2026-05-29

Goal reference:
`/home/jake/Developer/flashmask/goal/phase-5-backend-router.md`.

## Hardware Policy

Current completion is strict for the available NVIDIA RTX A6000 compute
capability 8.6 GPU. The router exposes SM80 and SM90/Hopper routes as
metadata-visible template paths, but they remain hard-gated until separate
SM80/A100 and H100/H200 hardware proof exists.

## Implemented Runtime Scope

```text
public request: auto
selected backend: fa2-compatible
backend kind: sm8x_sparse_fa2_compatible
device: SM86, compute capability 8.6
dtype: fp16, bf16
mask: PE non-causal state-autoregressive bound_num=2 interval masks
forward: implemented and verified
backward: implemented and verified
training: verified through PE tiny training and GPU parity tests
```

## Implementation Summary

- `src/flashmask/attention.py`
  - Makes `flashmask_attention(..., backend="auto")` the default public route.
  - Normalizes `auto`, `fa3`, `sm90-fa3`, `fa2-compatible`, and
    `sm8x-fa2-compatible`.
  - Selects the proven SM86 FA2-compatible sparse interval backend on the local
    A6000 build.
  - Leaves SM80 and SM90/Hopper paths fail-closed until their own runtime proof
    is recorded.
  - Adds requested/selected backend metadata, device name, capability alias,
    forward readiness, backward readiness, and SM8x support fields to
    `BackendInfo`.
  - Requires backward readiness for gradient-tracked public calls.

- `src/flashmask/proof.py`
  - Requires benchmark/proof records to include `selected_backend` alongside
    the requested backend and backend kind.

- `src/flashmask/bench_sm90.py`
  - Keeps the deferred Hopper harness pinned to `backend="fa3"`.
  - Emits `selected_backend` and `backward_ready` in gate and benchmark records.

- `/home/jake/Developer/pe/components/attention.py`
  - Adds `attention.FLASHMASK_BACKEND = "flashmask"` as PE's public FlashMask
    route.
  - Maps that route to package `backend="auto"`.
  - Keeps explicit `flashmask-fa2-compatible` and `flashmask-fa3` names for
    proof and diagnostics.

- `/home/jake/Developer/pe/benchmarks/bench_flashmask_attention.py`
  - Records `selected_backend` and `backward_ready` in benchmark artifacts.

## Verification Evidence

- FlashMask focused router/proof/package tests:
  - Command from `/home/jake/Developer/flashmask`:
    `uv run pytest -q tests/test_proof.py tests/test_package_surface.py tests/test_backend_contract.py`
  - Result: `83 passed in 2.23s`.

- FlashMask full CPU-safe regression suite:
  - Command from `/home/jake/Developer/flashmask`:
    `uv run pytest -q`
  - Result: `117 passed, 26 skipped in 2.42s`.

- Hard-gated SM86 optional CUDA tests:
  - Command from `/home/jake/Developer/pe`:
    `FLASHMASK_REQUIRE_SM86=1 uv run --extra gpu pytest -q /home/jake/Developer/flashmask/tests/test_cuda_extension_optional.py`
  - Result: `14 passed, 7 skipped in 3.32s`.

- Hard-gated SM8x optional CUDA tests:
  - Command from `/home/jake/Developer/pe`:
    `FLASHMASK_REQUIRE_SM8X=1 uv run --extra gpu pytest -q /home/jake/Developer/flashmask/tests/test_cuda_extension_optional.py`
  - Result: `14 passed, 7 skipped in 3.42s`.

- Runtime backend metadata in PE:
  - Command from `/home/jake/Developer/pe`:
    `uv run --extra gpu python - <<'PY' ...`
  - Result:
    `backend="auto"` reported `available=True`,
    `requested_backend="auto"`, `selected_backend="fa2-compatible"`,
    `backend_kind="sm8x_sparse_fa2_compatible"`, capability `(8, 6)`,
    `forward_ready=True`, `backward_ready=True`, and
    `training_available=True`.
  - Explicit `fa3` and `sm90-fa3` on the local A6000 reported unavailable with
    the SM90 compute capability requirement.

- PE targeted routing/training/parity tests:
  - Command from `/home/jake/Developer/pe`:
    `PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu pytest -q tests/test_attention.py tests/test_train.py tests/test_flashmask_verification_gates.py tests/test_flashmask_sm8x_gpu_parity.py`
  - Result: `56 passed in 19.46s`.

- PE Phase 5 hard-gated SM8x training/parity command:
  - Command from `/home/jake/Developer/pe`:
    `PYTHONPATH=/home/jake/Developer/flashmask/src PE_REQUIRE_FLASHMASK_SM8X=1 uv run --extra gpu pytest -q tests/test_train.py tests/test_flashmask_sm8x_gpu_parity.py`
  - Result: `33 passed in 4.10s`.

- PE full GPU-enabled regression suite:
  - Command from `/home/jake/Developer/pe`:
    `PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu pytest -q`
  - Result: `113 passed, 10 skipped in 19.67s`.

## Exit Criteria Status

- Backend selection for current SM86 hardware is proven through mocked router
  tests, hard-gated CUDA optional tests, and PE GPU parity/training tests.
- SM80 and SM90 routes remain present but fail closed until their separate
  proof state exists.
- Explicit architecture/backend mismatches fail with compute-capability-specific
  messages.
- Forward-only and backward-missing paths fail closed for training.
- FlashMask backends do not route through dense SDPA fallback.
- PE can use `--attention-backend flashmask` without choosing SM86 versus
  SM90 internally.
- Benchmark and proof records include requested backend, selected backend, and
  backend kind.

## Deferred Work

- SM80 runtime proof on A100-class hardware.
- SM90/Hopper runtime proof on H100/H200 hardware.
- Native GQA and block-mask support for selected sparse backends.
- Final speedup/proof artifacts remain Phase 7.

## Conclusion

Phase 5 is complete for the current hardware policy. FlashMask now exposes one
public auto-routed attention API for PE, selects the proven SM86 sparse interval
backend locally, records the router decision in metadata and proof artifacts,
and keeps SM80 plus SM90/Hopper routes templated and fail-closed until their
own hardware proof exists.
