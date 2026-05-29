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

Backend discovery reports unavailable unless the extension is built and the
active CUDA device can run the compiled sparse kernel.

On an SM90 machine with the experimental extension built, run the local parity
and timing harness with:

```bash
uv run flashmask-bench-sm90 --mode all --bench-seq-lens 2048 --heads 4 --head-dims 128 --dtypes fp16 --jsonl
```

It checks FlashMask output and LSE against a dense reference, confirms the
`fa3` backend, verifies that `flashmask::fwd` appears in a profiler trace, and
prints median FlashMask and dense SDPA timings.
