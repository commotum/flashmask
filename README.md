# flashmask

Standalone Python package for FlashMask sparse attention masks. This package
owns the interval representation described by "FlashMask: Efficient and Rich
Mask Extension of FlashAttention", dense reference reconstruction,
structured-mask builders, and the PE state-causal compiler. It intentionally
keeps the runtime surface small and framework-light.

The core mask object stores:

```python
startend_row_indices  # [B, mask_heads, seqlen_k, bound_num], int32-compatible
causal                # bool
```

``bound_num`` is one of ``1``, ``2``, or ``4``. Dense reconstruction returns a
boolean allowed-attention mask or an additive mask with ``0`` for allowed pairs
and ``-inf`` for masked pairs. Causal reconstruction follows FlashAttention
2.1+/3 bottom-right alignment when ``seqlen_q != seqlen_k``.

```python
from flashmask import IntervalMask

mask = IntervalMask([[[[4], [4], [4], [4]]]], causal=True)
allowed = mask.to_bool_mask()  # [B, H, Q, K]
```

PE metadata can be compiled without importing PE:

```python
from flashmask import compile_pe_state_causal_mask

mask = compile_pe_state_causal_mask(
    token_type_id=[[1, 2, 3, 3, 0]],
    time_index=[[-1, -1, 1, 2, -1]],
    valid_token=[[True, True, True, True, False]],
)
```

The CUDA attention backend is optional and fail-closed:

```python
from flashmask import flashmask_attention

# backend="auto" selects the verified local sparse backend when available.
flashmask_attention(q, k, v, mask)
```

Use ``to_bool_mask`` / ``to_additive_mask`` for correctness tests only; they are
not a fast attention path.

## Project Boundary

`flashmask` owns sparse interval masks, dense reference reconstruction,
structured mask constructors, PE metadata compilation into `IntervalMask`, the
backend router, and kernel-native attention calls. PE owns experiment,
model, train, eval, and rollout policy. `ankos` owns CA mechanics and rollout
outputs. Paddle and PaddleNLP are reference material only; they are not runtime
dependencies.

## Public API

The stable Python surface is exported from `flashmask`:

- `IntervalMask(startend_row_indices, causal=..., seqlen_q=...)` stores
  `[B, mask_heads, K, bound_num]` int32-compatible bounds.
- `to_bool_mask()` and `to_additive_mask()` reconstruct dense masks for tests
  and parity checks.
- `causal_mask`, `document_mask`, `prefix_lm_mask`, `sliding_window_mask`, and
  `from_dense_bool_mask` build common structured masks.
- `compile_dense_bool_mask` converts representable dense boolean masks and
  raises `MaskNotRepresentableError` for masks outside FlashMask interval
  form.
- `compile_pe_state_causal_mask` and `compile_pe_state_causal_query_mask`
  compile PE full-sequence and cached-query metadata without importing PE.
- `flashmask_attention`, `backend_info`, and `verify_backend` are the public
  backend route.
- `validate_proof_jsonl`, `validate_sm86_proof_jsonl`,
  `validate_sm80_proof_jsonl`, and `validate_sm90_proof_jsonl` validate
  benchmark artifacts.

The raw `torch.ops.flashmask.*` ABI is internal. It expects prevalidated CUDA
tensors and mask metadata; call `flashmask_attention` from application code.
Breaking changes after the `0.1.x` hardening line should include migration
notes.

## Kernel Backend

The kernel is the project priority. The Python package now has a lazy backend
loader and a PyTorch extension scaffold under `src/flashmask/csrc` with the
intended `flashmask::fwd` and `flashmask::bwd` op names.

The current verified public route is SM86 / compute capability 8.6 through the
FA2-compatible sparse interval backend. Calls with gradient-tracking Q/K/V fail
unless `verify_backend(require_backward=True)` succeeds, so training cannot
silently use a forward-only raw op.

Current experimental forward limits are reported by `backend_info()`: fp16/bf16,
head/value dimensions up to 128, expanded Q/K/V heads only, and no block-mask
metadata path yet. `backend_info()` defaults to `backend="auto"` and reports
both requested and selected backend names.

SM80 and SM90/Hopper routes are represented in metadata but remain hard-gated
until their own hardware proof exists. SM90 uses the FlashAttention
3-compatible sparse path when that future proof is enabled. SM86 uses the exact
interval-aware sparse kernel path because stock FA2 causal/window/padding
controls are not sufficient for PE's state-autoregressive mask.

## Build And Runtime Modes

The default install is pure Python and does not import torch, CUDA, Paddle, or
PaddleNLP at package import time. It supports mask construction, PE compilation,
dense reconstruction, backend readiness checks, and proof validation.

Supported build modes:

| Mode | Environment | Target | Forward | Backward | Wrong-hardware behavior |
| --- | --- | --- | --- | --- | --- |
| Pure Python | none | CPU/no extension | no | no | `verify_backend` fails closed |
| Stub extension | `FLASHMASK_BUILD_CUDA=1` | metadata smoke | no | no | raw ops raise not implemented |
| SM8x sparse | `FLASHMASK_BUILD_EXPERIMENTAL_SM8X_CUDA=1` plus `CUTLASS_HOME` or CUTLASS include env | SM86 current proof target; SM80 deferred | yes on proven SM86 | yes on proven SM86 | SM80 remains fail-closed until A100 proof |
| SM90 template | `FLASHMASK_BUILD_EXPERIMENTAL_CUDA=1` plus CUTLASS include env | Hopper template | deferred | no | fail-closed until H100/H200 proof |

Experimental CUDA builds require CUDA-enabled PyTorch, CUTLASS/CUTE headers,
fp16 or bf16 Q/K/V, head and value dimensions up to 128, expanded Q/K/V heads,
and no block-mask metadata path yet.

By default, installs do not build the CUDA extension. The stub extension can be
built from a CUDA-enabled PyTorch environment with:

```bash
FLASHMASK_BUILD_CUDA=1 uv pip install -e . --no-build-isolation -v
```

There is also a narrow experimental build for validating the current SM90
head_dim96/head_dim128 fp16/bf16 sparse forward wrapper:

```bash
FLASHMASK_BUILD_EXPERIMENTAL_CUDA=1 \
CUTLASS_INCLUDE_DIR=/path/to/cutlass/include \
uv pip install -e . --no-build-isolation -v
```

For local SM86/Ampere validation, build the experimental SM8x interval kernel
from a CUDA-enabled PyTorch environment. This path currently targets PE's
non-causal `bound_num=2` interval mask and applies exact interval masking inside
the SM80/86 FlashAttention mainloop. In the active kStages=1 SM86 path it scans
per query block and queues only K blocks that are not fully masked before K/V
loads and MMA; partial K blocks are still scored and masked after MMA.

```bash
FLASHMASK_BUILD_EXPERIMENTAL_SM8X_CUDA=1 \
CUTLASS_HOME=/path/to/cutlass \
uv pip install -e . --no-build-isolation -v
```

Backend discovery reports unavailable unless the extension is built and the
active CUDA device can run the compiled sparse kernel.

## Failure Modes

`verify_backend` and `flashmask_attention` are intentionally fail-closed. Error
messages include the requested backend, selected backend, compiled backend kind,
active compute capability, supported compute capabilities, forward/backward
readiness, CUDA availability, and a build command hint when known.

Common failures:

- missing extension: build an experimental extension for the target GPU, or use
  the pure Python mask utilities only.
- wrong GPU architecture: SM86 is the current strict proof target; SM80 and
  SM90 are deferred until their own hard-gated proof exists.
- missing CUTLASS path: set `CUTLASS_HOME`, `CUTLASS_INCLUDE_DIR`, or
  `FLASHMASK_CUTLASS_INCLUDE_DIR`.
- unsupported dtype/head dimension/GQA/block mask: use fp16 or bf16, head/value
  dimensions up to 128, expanded heads, and empty block-mask metadata.
- missing backward: remove gradient-tracking inputs or use a backend reporting
  `backward_ready=True`.
- dense mask passed to the backend: convert representable masks with
  `compile_dense_bool_mask` or use dense helpers for tests only.
- profiler proof missing expected kernel markers: rerun the hard-gated proof
  command without profiler skipping.

## PE Integration

In PE, use an editable dependency and point Python at this package while
validating local changes:

```bash
PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu pytest -q tests/test_flashmask_sm8x_gpu_parity.py tests/test_flashmask_verification_gates.py
```

PE should compile metadata into `IntervalMask`, call `flashmask_attention`,
check `backend_info()` or `verify_backend()` before sparse runs, use dense
helpers only for parity, and run hard-gated GPU proof tests for the selected
architecture. PE should not import CUDA source files or reference-repo code.

On an SM90 / compute capability 9.0 machine with the experimental extension
built, run the local parity and timing harness with:

```bash
uv run flashmask-bench-sm90 --require-sm90 --mode all --bench-seq-lens 2048 --heads 4 --head-dims 128 --dtypes fp16 --min-speedup 1.15 --jsonl --output-jsonl artifacts/flashmask-sm90.jsonl
uv run flashmask-validate-sm90-proof --min-speedup 1.15 --require-case bench artifacts/flashmask-sm90.jsonl
```

It checks FlashMask output and LSE against a dense reference, confirms the
`fa3` backend, verifies that `flashmask::fwd` and the expected FlashMask CUDA
kernel markers appear in a profiler trace, and records median public-API,
raw-op, and dense SDPA timings.
The proof record is self-describing: the validator requires
`requested_backend=fa3`, `backend_kind=sm90_sparse_fa3`, the exact SM90 CUDA
marker list, parity metrics, no profiler skip, and no dense attention events.

The proof validator is backend-aware; PE's SM86 benchmark artifacts can be
validated with:

```bash
cd /home/jake/Developer/pe
PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu flashmask-validate-proof --backend fa2-compatible --min-speedup 1.5 --require-case full --require-case rollout artifacts/pe-flashmask-sm86.jsonl
```

That combined proof is assembled from
`artifacts/pe-flashmask-sm86-full.jsonl` and
`artifacts/pe-flashmask-sm86-rollout.jsonl`. The current rollout proof uses the
default token-capped PE eval batch shape for 4096-token episodes (`B=32`,
`Q=128`, `K=3970`). Training speed is not claimed without a separate train-step
proof.

For CI or an explicit SM90 validation run, make optional GPU tests fail instead
of skip when the backend is unavailable:

```bash
FLASHMASK_REQUIRE_SM90=1 uv run pytest -q tests/test_cuda_extension_optional.py tests/test_cuda_pe_parity_optional.py
```

For local SM86 validation, use the SM8x build and require the SM86 optional
tests:

```bash
FLASHMASK_REQUIRE_SM86=1 uv run pytest -q tests/test_cuda_extension_optional.py
```

SM80/A100 and SM90/Hopper proof jobs should be separate hard-gated jobs and
must not reuse local SM86/A6000 proof artifacts to claim support.

## Dependency And Artifact Hygiene

Runtime metadata declares no package dependencies. Generated artifacts such as
benchmark JSONL, profiler traces, build directories, wheels, and egg-info are
ignored unless intentionally versioned. Source distributions include the CUDA
and C++ sources needed for extension builds; wheels exclude the CUDA source
snapshot and large reference material.
