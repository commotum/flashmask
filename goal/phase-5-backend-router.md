# Phase 5: Backend Router

## Pasteable Goal

Expose one public FlashMask attention API with an internal backend router that
selects the current SM80/SM86 exact sparse interval backend on supported local
hardware, while keeping SM90 FA3-compatible routing as a templated,
fail-closed Hopper path. See
`/home/jake/Developer/flashmask/goal/phase-5-backend-router.md` for the
detailed scope, tests, and exit criteria.

## Objective

Expose one stable public attention API that selects the correct sparse backend
for the active GPU and build, with SM86/SM8x as the strict current runtime
target.

The router should make FlashMask easy to consume from PE: PE should ask for
FlashMask once, and the package should choose the correct kernel-native sparse
implementation for the current machine. Unsupported combinations must fail
closed.

SM90/Hopper routing should be present as a template path: aliases, backend
metadata, architecture checks, and hard-gated H100/H200 validation commands
should exist, but real SM90 runtime routing is not a current completion
criterion until Hopper hardware is available.

## Non-Goals

- Do not add dense SDPA fallback to make unsupported machines "work".
- Do not hide architecture or backend mismatch errors.
- Do not make PE responsible for choosing SM90 versus SM80/SM86 kernels.
- Do not report a backend as training-capable unless backward is ready.

## Public Shape

```python
flashmask_attention(
    q,
    k,
    v,
    mask,
    *,
    backend="auto",
    softmax_scale=None,
)
```

Supported backend requests:

- `auto`
- `fa3`
- `sm90-fa3`
- `fa2-compatible`
- `sm8x-fa2-compatible`

Aliases are acceptable, but they must normalize to a small set of canonical
backend names.

## Canonical Backend Kinds

Suggested canonical backend kinds:

- `sm90_sparse_fa3`
- `sm8x_sparse_fa2_compatible`
- `stub`
- `unavailable`

The public API should distinguish:

- requested backend name
- selected backend name
- compiled backend kind
- active GPU capability
- forward readiness
- backward readiness

## Routing Rules

For `backend="auto"`:

- SM80/SM86:
  - route to `sm8x_sparse_fa2_compatible` if compiled and forward-ready
  - fail closed if the SM80/SM86 backend is not compiled or not ready
- SM90 / compute capability 9.0:
  - keep the route to `sm90_sparse_fa3` defined for later Hopper validation
  - fail closed if the SM90 backend is not compiled, not ready, or not
    separately proven on Hopper hardware
- unsupported GPU:
  - fail closed with an actionable message
- CPU tensors:
  - fail closed for kernel-native attention

For explicit backend requests:

- `fa3`/`sm90-fa3` requires SM90, the FA3-compatible sparse backend, and
  deferred Hopper proof before it may be treated as runtime-ready.
- `fa2-compatible`/`sm8x-fa2-compatible` requires a supported SM80/SM86-class
  GPU and the exact sparse interval backend.
- architecture mismatch must fail clearly.
- build mismatch must fail clearly.

The router must never choose a backend that changes mask semantics.

## Forward And Training Readiness

Router calls should validate readiness according to the operation:

- inference forward requires `forward_ready=True`
- training forward with any gradient-tracked Q/K/V requires
  `backward_ready=True`
- explicit `verify_backend(require_backward=True)` must fail for
  inference-only builds

This prevents PE from accidentally training with a forward-only sparse path.

## Backend Info API

Expose enough metadata for tests, logs, and benchmarks:

```python
info = backend_info()
```

Minimum fields:

- `requested_backend`
- `selected_backend`
- `backend_kind`
- `module_path`
- `cuda_available`
- `device_name`
- `capability`
- `forward_ready`
- `backward_ready`
- `is_fa3`
- `supports_sm8x`
- `supports_native_gqa`
- `supports_block_mask`

Also expose:

```python
verify_backend(
    backend="auto",
    require_sparse=True,
    require_forward=True,
    require_backward=False,
    require_fa3=False,
)
```

`verify_backend` should return backend info on success and raise an actionable
error on failure.

## Error Requirements

Failures must identify:

- requested backend
- selected backend, if any
- active compute capability
- compiled backend kind
- missing capability, such as forward or backward
- expected build flag or install mode when useful

Examples:

- "backend='fa3' requires SM90 / compute capability 9.0, got (8, 6)"
- "SM86 sparse backend is not compiled; rebuild with ..."
- "FlashMask backend is forward-only; backward is required for training"
- "native GQA is not supported by selected backend"

## GQA And Head Routing

The router should not silently rely on unsupported native GQA.

Allowed strategies:

- selected backend supports native GQA and reports it
- Python wrapper expands K/V heads before the op and records that expansion
- unsupported GQA fails clearly

Whichever strategy is used must be tested and observable.

## Block Mask Routing

If `block_mask` is exposed:

- SM90 may support it when the FA3-compatible backend proves support.
- SM80/SM86 may reject it until explicitly implemented.
- router errors must say whether the selected backend supports block masks.

No backend should accept `block_mask` and ignore it.

## Observability

Routing decisions must be visible in:

- returned backend info
- benchmark JSONL artifacts
- proof validator records
- optional debug logging or profiler metadata
- PE integration tests

Benchmark/proof records should include:

- requested backend
- selected backend
- backend kind
- GPU capability
- forward/backward readiness
- whether GQA expansion occurred
- whether block mask was used

## PE Contract

PE should use only the public FlashMask API and PE-facing backend names.

PE should not:

- inspect CUDA source details
- decide SM90 versus SM86 implementation details
- fall back from FlashMask to dense SDPA after requesting FlashMask
- enable training unless FlashMask reports backward readiness

PE may:

- select dense SDPA explicitly as a separate reference backend
- request FlashMask explicitly for tests/benchmarks
- record backend info in artifacts

## Tests

Required tests:

- `auto` selects SM80/SM86 backend on mocked SM86 capability.
- SM90 alias/routing metadata is present and can be tested with mocks, but
  real SM90 runtime routing is deferred until Hopper validation.
- unsupported capability fails clearly.
- explicit `fa3` on SM86 fails clearly.
- explicit `fa2-compatible` on SM90 fails clearly unless explicitly supported.
- missing extension fails clearly.
- stub extension fails clearly.
- forward-only backend rejects gradient-tracked training calls.
- inference call accepts forward-ready backend.
- no dense SDPA fallback is reachable through FlashMask backends.
- backend info records requested and selected backend.
- benchmark/proof record helpers include backend routing fields.

Optional GPU tests:

- real SM86 routing uses the exact sparse interval kernel.
- profiler evidence matches selected backend.
- deferred SM90/Hopper routing proof uses the FA3-compatible kernel when
  `FLASHMASK_REQUIRE_SM90=1` is run on H100/H200 hardware.

## Test Commands

CPU-safe router tests:

```bash
uv run pytest -q
```

Optional GPU router tests:

```bash
FLASHMASK_REQUIRE_SM8X=1 uv run pytest -q tests/test_cuda_extension_optional.py
```

Deferred Hopper router proof:

```bash
FLASHMASK_REQUIRE_SM90=1 uv run pytest -q tests/test_cuda_extension_optional.py
```

PE-side routing tests after integration:

```bash
PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu pytest -q tests/test_attention.py tests/test_flashmask_gpu_parity.py
```

## Exit Criteria

- Tests prove backend selection for the current supported SM80/SM86-class
  architecture.
- Tests prove explicit backend mismatch fails clearly.
- Tests prove missing forward/backward readiness fails closed.
- Tests prove no dense fallback is reachable through FlashMask backends.
- PE can call one FlashMask API without architecture-specific branching.
- Benchmark artifacts record requested backend, selected backend, and backend
  kind.
- SM90/Hopper routing remains a documented, fail-closed template path until
  separate H100/H200 runtime proof exists.
- The router is ready for PE integration and Phase 7 proof artifacts.
