# Phase 0-1 Reference Map

Goal: map the FlashMask paper/reference implementation into the standalone
package responsibilities before porting kernels or deepening PE integration.

Details: `/home/jake/Developer/flashmask/goal/phase-0-1-reset-and-mask-spec.md`

## Source Of Truth For Phase 0-1

The Phase 0-1 source of truth is the standalone interval-mask contract:

- Canonical metadata shape is `[B, mask_heads, K, bound_num]`.
- `bound_num` is `1`, `2`, or `4`.
- Bounds are `int32` query-row indices for each key column.
- Dense reconstruction must define `True` as an allowed Q/K interaction.
- PE state-autoregressive semantics must match PE's dense mask exactly.
- FlashMask must not import PE or large framework runtimes to compile masks.

## Reference Files

`/home/jake/Developer/flashmask/context/generate_startend_row_indices.py`

- Role: local reference copy of interval semantics and common mask examples.
- Phase 0-1 action: conceptual reference only. Re-implement semantics in pure
  Python package code, not by importing this file.
- Standalone mapping:
  - `startend_row_indices_to_attn_bias` maps to
    `flashmask.core.dense_bool_from_intervals`.
  - Common mask generators map to `flashmask.builders`.

`/home/jake/Developer/flashmask/context/masks.py`

- Role: dense examples for the mask families the package should represent.
- Phase 0-1 action: use as test input and expected dense behavior.
- Standalone mapping:
  - `tests/test_builders.py` compares builders and dense compilation against
    these examples.

`/home/jake/Developer/flashmask/sub/Paddle/test/test_flashmask_ci/generate_startend_row_indices.py`

- Role: upstream-style executable dense semantics for `startend_row_indices`.
- Phase 0-1 action: conceptual reference for the interval convention.
- Standalone mapping:
  - Do not import this file at runtime or in core tests.
  - Preserve the same lower/upper interval convention in `IntervalMask`.

`/home/jake/Developer/flashmask/sub/Paddle/python/paddle/nn/functional/flash_attention.py`

- Role: public API reference for `startend_row_indices` shape, validation, and
  split between legacy and v2 FlashMask attention paths.
- Phase 0-1 action: adapt only the mask metadata contract and validation ideas.
- Standalone mapping:
  - `flashmask.core.normalize_startend_row_indices` owns shape and dtype
    validation.
  - `flashmask.attention.flashmask_attention` should remain fail-closed until
    Phases 2-5 provide real kernels and routing.

`/home/jake/Developer/flashmask/sub/Paddle/paddle/phi/kernels/gpu/flash_attn_kernel.cu`

- Role: FA2-era reference showing interval tensor slicing and
  `flashmask_maxmin` handoff into attention.
- Phase 0-1 action: kernel-port reference only.
- Standalone mapping:
  - No Phase 0-1 runtime dependency.
  - Later phases should preserve the algorithmic move: preprocess token-level
    intervals into block-level min/max metadata so fully masked tiles can be
    skipped.

`/home/jake/Developer/flashmask/sub/Paddle/paddle/phi/kernels/gpu/flash_attn_v3_kernel.cu`

- Role: FA3-era FlashMask v2 forward reference, including block mask handling,
  interval slicing, max/min metadata, parameter population, and sparse forward
  launch.
- Phase 0-1 action: kernel-port reference only.
- Standalone mapping:
  - Phase 3 should port/adapt the forward sparse kernel route.
  - Phase 5 should expose backend selection through one public FlashMask API.

`/home/jake/Developer/flashmask/sub/Paddle/paddle/phi/kernels/gpu/flash_attn_v3_grad_kernel.cu`

- Role: FA3-era FlashMask v2 backward reference.
- Phase 0-1 action: backward-port reference only.
- Standalone mapping:
  - Phase 4 should port/adapt backward and autograd integration.
  - Phase 0-1 must not claim training readiness from this file.

`/home/jake/Developer/flashmask/sub/PaddleNLP/paddlenlp/transformers/llama/fusion_ops.py`

- Role: downstream model-layer handoff reference: model code forwards
  `attn_mask_startend_row_indices` into FlashMask attention instead of building
  dense masks.
- Phase 0-1 action: conceptual boundary reference.
- Standalone mapping:
  - PE should mirror the boundary, not the framework code: PE compiles or
    forwards interval metadata, then calls FlashMask through its standalone API.

## PE-Specific Mapping

`/home/jake/Developer/pe/components/batch.py`

- Dense policy source:
  - `build_state_causal_query_mask`
  - `build_state_causal_attention_mask_batch`
- FlashMask adapter:
  - `compile_state_causal_flashmask_mask`
  - `compile_state_causal_flashmask_query_mask`
  - `flashmask_pe_token_types`
- Required equivalence:
  - FlashMask reconstructed dense masks must equal the PE dense policy for full
    batches and incremental query blocks.

`/home/jake/Developer/flashmask/src/flashmask/pe.py`

- Standalone implementation:
  - `compile_pe_state_causal_mask`
  - `compile_pe_state_causal_query_mask`
  - `dense_pe_state_causal_mask`
  - `dense_pe_state_causal_query_mask`
- Required boundary:
  - Accept plain sequences or array-like values.
  - Do not import PE.
  - Do not import Torch.
  - Raise `MaskNotRepresentableError` if the PE query/key ordering cannot be
    represented as one contiguous allowed interval per key.

## Kernel-Native Requirement To Preserve

Later kernel phases must preserve the central FlashMask behavior from the paper:
the visibility pattern is encoded as sparse interval/block metadata before score
computation. Fully masked tiles are skipped, fully visible tiles avoid per-token
mask work, and partial tiles apply the interval mask inside the kernel.

Dense reconstruction in Phase 0-1 is only a test oracle. It is not a fast path
and should never be used as proof of kernel-native sparse attention.
