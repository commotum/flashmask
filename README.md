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

The CUDA/FA3 attention backend is optional and fail-closed:

```python
from flashmask import flashmask_attention

# Raises NotImplementedError unless a compatible kernel backend is available.
flashmask_attention(q, k, v, mask)
```

Use ``to_bool_mask`` / ``to_additive_mask`` for correctness tests only; they are
not a fast attention path.

## Kernel Backend

The kernel is the project priority. The Python package now has a lazy backend
loader and a PyTorch extension scaffold under `src/flashmask/csrc` with the
intended `flashmask::fwd` and `flashmask::bwd` op names.

The experimental SM90 path is forward-only. Calls with gradient-tracking Q/K/V
fail unless `verify_backend(require_backward=True)` succeeds, so training cannot
silently use a forward-only raw op.

Current experimental forward limits are reported by `backend_info()`: fp16/bf16,
head/value dimensions up to 128, expanded Q/K/V heads only, and no block-mask
metadata path yet.

SM90 uses the FlashAttention 3-compatible sparse path. SM86 is also a required
target, but it needs an exact interval-aware sparse kernel path; stock FA2
causal/window/padding controls are not sufficient for PE's state-autoregressive
mask.

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
uv run flashmask-validate-proof --backend fa2-compatible --min-speedup 1.5 --require-case full /home/jake/Developer/pe/artifacts/pe-flashmask-sm86.jsonl
```

For CI or an explicit SM90 validation run, make optional GPU tests fail instead
of skip when the backend is unavailable:

```bash
FLASHMASK_REQUIRE_SM90=1 uv run pytest -q tests/test_cuda_extension_optional.py tests/test_cuda_pe_parity_optional.py
```
