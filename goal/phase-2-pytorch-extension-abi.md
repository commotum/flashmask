# Phase 2: PyTorch Extension ABI

## Pasteable Goal

Lock down and test the standalone PyTorch extension ABI for FlashMask,
including `torch.ops.flashmask.fwd/bwd`, backend metadata, build modes, and
fail-closed behavior, before treating any experimental kernel path as a stable
backend. See
`/home/jake/Developer/flashmask/goal/phase-2-pytorch-extension-abi.md` for the
detailed scope, tests, and exit criteria.

## Objective

Lock down and test the standalone PyTorch extension surface before treating any
kernel path as stable.

This phase should produce a stable ABI that the SM90 and SM86 kernel ports
can target. Experimental kernel code may already exist in the tree; Phase 2 is
where the public Python API, raw torch op signatures, backend metadata, build
modes, and fail-closed behavior become the contract that later phases must not
casually redesign.

## Non-Goals

- Do not port real forward or backward kernels in this phase.
- Do not add dense SDPA fallback behind the FlashMask API.
- Do not depend on Paddle/PaddleNLP runtime code, tensor wrappers, op registry,
  or dynload helpers.
- Do not make PE training appear supported before backward exists.

## Public Python API Target

The package should expose one stable user-facing attention function:

```python
flashmask_attention(q, k, v, mask, *, backend="fa3", softmax_scale=None)
```

Expected behavior:

- explicit backends such as `"fa3"` and `"fa2-compatible"` validate
  architecture and build support before the raw torch op is called.
- `backend="auto"` is a Phase 5 router behavior. Phase 2 should reserve room
  for it in the public API shape, but it does not need to implement automatic
  backend selection.
- unavailable sparse kernels raise actionable errors.
- the function never silently falls back to dense SDPA masking.

The Python API should wrap a PyTorch autograd function once backward exists, but
the ABI should be shaped for that from the start.

## Torch Op Surface

Register operators under the `flashmask` namespace:

```text
torch.ops.flashmask.fwd(...)
torch.ops.flashmask.bwd(...)
```

Forward signature:

```text
fwd(
    q: Tensor,
    k: Tensor,
    v: Tensor,
    startend: Tensor,
    block_mask: Tensor,
    softmax_scale: float,
    causal: bool
) -> (out: Tensor, softmax_lse: Tensor)
```

Backward signature:

```text
bwd(
    dout: Tensor,
    q: Tensor,
    k: Tensor,
    v: Tensor,
    out: Tensor,
    softmax_lse: Tensor,
    startend: Tensor,
    block_mask: Tensor,
    softmax_scale: float,
    causal: bool,
    deterministic: bool
) -> (dq: Tensor, dk: Tensor, dv: Tensor)
```

The raw torch ops do not take a backend string. The installed extension reports
its backend kind through metadata, and the Python wrapper validates that the
requested backend matches the loaded extension before calling
`torch.ops.flashmask.fwd/bwd`.

Optional `block_mask` at the Python level is represented as an empty int32
tensor at the raw op boundary until block-mask kernels are implemented.

## Tensor Layout Contract

Inputs:

- `q`: `[B, Q, Hq, D]`
- `k`: `[B, K, Hkv, D]`
- `v`: `[B, K, Hkv, D]`
- `startend`: `[B, Hmask, K, bound_num]`, `int32`
- `block_mask`: optional block-level mask, backend-specific

Outputs:

- `out`: `[B, Q, Hq, D]`
- `softmax_lse`: shape chosen to match kernel needs, documented before Phase 3

Supported dtypes for kernel paths:

- `float16`
- `bfloat16`

Initial supported head dimensions:

- `D <= 128`, unless a backend explicitly proves more.

Head handling:

- `Hmask` may be `1` or match/broadcast across attention heads.
- Native GQA support should be explicit. If a backend cannot support native
  `Hq != Hkv`, it must fail clearly or require the Python wrapper to expand K/V
  before calling the op.

Stride handling:

- The ABI should define whether tensors must be contiguous.
- If non-contiguous tensors are accepted, tests must prove correct stride
  handling.
- If contiguous tensors are required initially, Python wrappers should call
  `.contiguous()` intentionally and tests should document that behavior.

## Mask Metadata Contract

`startend` follows the Phase 0-1 `IntervalMask` spec:

```text
[batch, mask_heads, seqlen_k, bound_num]
bound_num in {1, 2, 4}
dtype int32
```

The extension ABI consumes interval metadata directly. It must not consume a
dense boolean attention mask as the fast path.

The ABI should reserve room for:

- lower-triangle start/end pointers
- upper-triangle start/end pointers
- `flashmask_maxmin` or equivalent preprocessing output
- optional `block_mask`
- backend-specific scheduler metadata if needed later

Phase 2 does not need to finalize every internal struct or preprocessing kernel,
but it must avoid an ABI that blocks the known FA2-compatible and FA3-compatible
ports.

## Backend Metadata

Expose backend introspection from Python:

```python
backend_info()
verify_backend(...)
```

Minimum metadata:

- selected backend name
- backend kind
- compiled extension path
- CUDA availability
- current compute capability
- whether forward is ready
- whether backward is ready
- whether native GQA is supported
- whether FA3-compatible mode is supported
- whether the SM86/SM8x sparse mode is supported

Suggested backend kinds:

- `stub`
- `sm90_sparse_fa3`
- `sm8x_sparse_fa2_compatible`

The Python layer should map public backend names to backend kinds, for example:

- `"fa3"` -> `sm90_sparse_fa3`
- `"fa2-compatible"` -> `sm8x_sparse_fa2_compatible`

## Build Modes

Support explicit build modes:

- no CUDA extension: pure Python package imports and mask tests run
- stub extension: registers ops and fails closed
- SM90 extension: builds FA3-compatible sparse path
- SM86/SM8x extension: builds exact sparse interval path for the supported
  compute capabilities

Build flags should be clear and mutually exclusive where needed. Example names
are acceptable if already present in the codebase:

- `FLASHMASK_BUILD_EXPERIMENTAL_CUDA=1`
- `FLASHMASK_BUILD_EXPERIMENTAL_SM8X_CUDA=1`

If a generic flag such as `FLASHMASK_BUILD_CUDA=1` is introduced, it should map
unambiguously to one of the explicit build modes or fail with an actionable
message.

The setup/build code must explain missing CUDA, missing PyTorch CUDA, missing
CUTLASS, and unsupported architecture errors.

## Fail-Closed Rules

Every unavailable capability must fail closed:

- no extension built
- wrong GPU architecture
- backend not compiled
- forward kernel missing
- backward kernel missing
- unsupported dtype
- unsupported head dimension
- unsupported native GQA
- invalid `startend` shape or dtype

Failures should be actionable and should not route to dense SDPA.

## Autograd Shape

Even before real backward exists, Phase 2 should establish how autograd will
work:

- forward saves Q/K/V, output, LSE, mask metadata, scale, causal flag, and
  backend selection as needed.
- backward calls `torch.ops.flashmask.bwd`.
- if `requires_grad` is true and `backward_ready` is false, the call fails
  before pretending training is supported.

This prevents PE from accidentally training through an inference-only path.

## Tests

CPU-safe tests:

- package imports without extension
- stub extension registers expected symbols when built
- `backend_info()` reports stub/unavailable states correctly
- `verify_backend(...)` fails when forward is unavailable
- `verify_backend(require_backward=True)` fails when backward is unavailable
- public API does not call dense SDPA fallback
- invalid mask metadata fails before kernel launch
- unsupported dtype/head dimension/GQA errors are explicit
- build mode flags are documented and mutually exclusive

Optional GPU tests:

- hard-gated raw op smoke test for each compiled backend
- architecture mismatch fails loudly
- forward-required gate fails if sparse kernel is not ready
- backward-required gate fails if backward is not ready

Representative commands:

```bash
uv run pytest -q
```

For explicit extension smoke tests:

```bash
FLASHMASK_BUILD_CUDA=1 uv pip install -e . --no-build-isolation -v
uv run pytest -q tests/test_cuda_extension_optional.py
```

## Exit Evidence

This phase is complete only when:

- the Python API and torch op signatures are documented
- the implemented raw torch op signatures match this document, or this document
  is updated with the intentional final ABI before Phase 3 begins
- stub/no-extension installs import cleanly
- fail-closed tests pass
- backend metadata reports forward/backward readiness accurately
- no Paddle/PaddleNLP runtime symbols are required
- the ABI is sufficient for Phase 3 forward kernels and Phase 4 backward
  without another public API redesign
