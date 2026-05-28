# FlashMask Interface Notes

This note documents only the code that forms the interface between FlashMask and FlashAttention in the local `Paddle` and `PaddleNLP` submodules.

It intentionally ignores most of the training stack, distributed training setup, RLHF plumbing, and model-family boilerplate unless that code directly:

- builds `attn_mask_startend_row_indices`
- preserves or offsets those indices during packing / padding
- decides to call FlashMask instead of dense-mask attention
- forwards the sparse mask into Paddle's FlashMask kernels

## Short Answer

FlashMask here is not a separate attention implementation. It is a sparse mask ABI layered on top of FlashAttention:

1. `PaddleNLP` builds or forwards `attn_mask_startend_row_indices`.
2. `paddle.nn.functional.flashmask_attention(...)` validates that sparse mask and picks the FA2 or FA3 route.
3. `Paddle` slices the last dimension of `startend_row_indices` into lower/upper interval tensors.
4. Those interval pointers, plus a `flashmask_maxmin` scratch tensor, are passed into:
   - FA2 through `dynload::flash_attn_fwd` / `dynload::flash_attn_bwd`
   - FA3 through a dedicated `flashmaskv2_*` parameter-handle API in `libflashmaskv2.so`

The real algorithm seam is the sparse mask representation and how it is converted into the kernel ABI.

## Mask Representation

The core sparse mask tensor is `startend_row_indices`.

- Public API: `Paddle/python/paddle/nn/functional/flash_attention.py:1298`
- Shape docs: `Paddle/python/paddle/nn/functional/flash_attention.py:1345`
- Sparse-mask dimension normalization: `Paddle/paddle/phi/kernels/gpu/flash_attn_utils.h:87`

Canonical kernel shape:

- `[batch, mask_heads, k_seq_len, bound_num]`
- `bound_num` must be `1`, `2`, or `4`
- dtype must be `int32`

Semantics:

- `causal=True`, `bound_num=1`: `[lt_start]`
- `causal=True`, `bound_num=2`: `[lt_start, lt_end]`
- `causal=False`, `bound_num=2`: `[lt_start, ut_end]`
- `causal=False`, `bound_num=4`: `[lt_start, lt_end, ut_start, ut_end]`

Kernel naming:

- FA2 code names these pieces `downstart`, `downend`, `upstart`, `upend`
- FA3 code names them `lt_start`, `lt_end`, `ut_start`, `ut_end`

This is the code form of the paper's column-wise interval mask.

## PaddleNLP: Where The Sparse Mask Comes From

### Standard SFT / causal packed data

The simplest causal FlashMask case is generated as a 1D vector of row starts:

- `PaddleNLP/llm/utils/data.py:242`
- `PaddleNLP/llm/utils/data.py:343`

When `zero_padding=True` and `flash_mask=True`, PaddleNLP emits:

```python
features["attn_mask_startend_row_indices"] = [seq_length] * seq_length
```

Instead of a dense triangular `attention_mask`.

### Packed zero padding

When multiple records are packed together, those row indices are offset by the running packed length:

- `PaddleNLP/paddlenlp/datasets/zero_padding_dataset.py:84`

This is a critical step. Without it, the sparse mask would still point at per-sample row coordinates instead of packed-sequence row coordinates.

### Tokenizer padding

Tokenizer padding preserves and reshapes the sparse mask:

- `PaddleNLP/paddlenlp/transformers/tokenizer_utils_base.py:3374`

Important behavior:

- raw `[seq_len]` is converted to `[1, seq_len]`
- right padding appends zeros
- left padding prepends zeros and adds the left-pad offset to existing entries
- final shape is asserted to be `[num_head, seq_len]`

### More interesting interval builders

Not all callers use the trivial causal `[seq_len] * seq_len` pattern.

Nontrivial sparse masks are built in:

- `PaddleNLP/paddlenlp/trl/trl_data.py:166`
- `PaddleNLP/llm/alignment/rm/data.py:98`
- `PaddleNLP/llm/alignment/rm/data.py:251`
- `PaddleNLP/paddlenlp/rl/models/ppo_model_utils.py:241`
- `PaddleNLP/paddlenlp/rl/utils/bert_padding.py:168`

These paths matter if you want examples of interval construction beyond plain causal masking.

## PaddleNLP: Where FlashMask Is Selected

The main decision point is:

- `PaddleNLP/paddlenlp/transformers/llama/fusion_ops.py:258`

Behavior:

- if `attn_mask_startend_row_indices is not None`, use FlashMask
- else use plain `F.scaled_dot_product_attention`
- if `F.flashmask_attention` is unavailable, fall back to legacy `F.flash_attention_with_sparse_mask`

For the common path, PaddleNLP reshapes:

- `[B, L] -> [B, 1, L]`
- then `unsqueeze(-1)` to `[B, 1, L, 1]`

and calls:

- `F.flashmask_attention(...)` at `PaddleNLP/paddlenlp/transformers/llama/fusion_ops.py:263`

This is the most important PaddleNLP file if you only care about the FlashMask handoff.

### Model wrappers that route into the common helper

Representative call sites:

- Llama: `PaddleNLP/paddlenlp/transformers/llama/modeling.py:220`
- Qwen2: `PaddleNLP/paddlenlp/transformers/qwen2/modeling.py:184`
- DeepSeek-V2 auto: `PaddleNLP/paddlenlp/transformers/deepseek_v2/modeling_auto.py:97`

Representative wrapper behavior:

- sparse mask wins over dense `attention_mask`
- dense causal mask preparation is skipped when sparse indices are present

Examples:

- `PaddleNLP/paddlenlp/transformers/llama/modeling.py:1720`
- `PaddleNLP/paddlenlp/transformers/llama/modeling.py:2118`
- `PaddleNLP/paddlenlp/transformers/qwen2/modeling.py:1315`
- `PaddleNLP/paddlenlp/transformers/qwen2/modeling.py:1652`

### Direct PaddleNLP FlashMask call sites

There are also direct `F.flashmask_attention(...)` call sites in the Llama network path:

- `PaddleNLP/paddlenlp/transformers/llama/modeling_network.py:123`
- `PaddleNLP/paddlenlp/transformers/llama/modeling_network.py:221`

These are simpler than the common `fusion_ops.py` path but conceptually do the same thing.

## Paddle Public API

Public API entry:

- `Paddle/python/paddle/nn/functional/flash_attention.py:1298`

This function:

- validates `startend_row_indices`
- optionally synthesizes sparse masks from `window_size`
- resolves distributed overlap metadata from `group`
- picks FA2 or FA3 based on `FLAGS_flash_attn_version` and device constraints

Key dispatch points:

- FA2: `_C_ops.flashmask_attention(...)` at `Paddle/python/paddle/nn/functional/flash_attention.py:2120`
- FA3: `_C_ops.flashmask_attention_v2(...)` at `Paddle/python/paddle/nn/functional/flash_attention.py:2166`

FA3-only public extras:

- `block_mask`
- `unique_id`
- `softmax_scale`
- `rank`
- `nranks`

Distributed overlap helpers:

- `_get_or_create_unique_id(...)`: `Paddle/python/paddle/nn/functional/flash_attention.py:1259`
- `flashmask_get_unique_id()`: `Paddle/python/paddle/nn/functional/flash_attention.py:2192`

## Op Surface

Forward op schemas:

- `Paddle/paddle/phi/ops/yaml/ops.yaml:2172` for `flashmask_attention`
- `Paddle/paddle/phi/ops/yaml/ops.yaml:2186` for `flashmask_attention_v2`

Backward schemas:

- `Paddle/paddle/phi/ops/yaml/backward.yaml:1245` for `flashmask_attention_grad`
- `Paddle/paddle/phi/ops/yaml/backward.yaml:1257` for `flashmask_attention_v2_grad`

Interpretation:

- `flashmask_attention` is the FA2-era interface
- `flashmask_attention_v2` is the dedicated FA3-era interface

## Paddle FA2 Path

Core forward file:

- `Paddle/paddle/phi/kernels/gpu/flash_attn_kernel.cu`

Core backward file:

- `Paddle/paddle/phi/kernels/gpu/flash_attn_grad_kernel.cu`

### Forward

The key wrapper is:

- `FlashAttnBaseKernel(...)` at `Paddle/paddle/phi/kernels/gpu/flash_attn_kernel.cu:333`

FlashMask-specific work:

- validate sparse mask rank and last-dim size: `:432`
- allocate `flashmask_maxmin`: `:447`
- slice the last axis into interval tensors: `:453`
- pass interval pointers plus `flashmask_maxmin` into `dynload::flash_attn_fwd(...)`: `:571`

The dedicated op entry is:

- `FlashMaskKernel(...)` at `Paddle/paddle/phi/kernels/gpu/flash_attn_kernel.cu:718`

It simply forwards into `FlashAttnBaseKernel(...)` with `startend_row_indices` set and dense `attn_mask` unset.

### Backward

The backward mirror is:

- `FlashAttnGradBaseKernel(...)` at `Paddle/paddle/phi/kernels/gpu/flash_attn_grad_kernel.cu:570`

FlashMask-specific work is the same pattern:

- validate sparse mask
- allocate `flashmask_maxmin`
- slice interval tensors
- pass them into `dynload::flash_attn_bwd(...)`

The dedicated op entry is:

- `FlashMaskGradKernel(...)` at `Paddle/paddle/phi/kernels/gpu/flash_attn_grad_kernel.cu:1013`

### FA2 registration

- forward kernel registration: `Paddle/paddle/phi/kernels/gpu/flash_attn_kernel.cu:811`
- backward kernel registration: `Paddle/paddle/phi/kernels/gpu/flash_attn_grad_kernel.cu:1086`

## Paddle FA3 Path

Important caveat:

- the generic FA3 `flash_attn` path explicitly rejects FlashMask and dense masks in `Paddle/paddle/phi/kernels/gpu/flash_attn_kernel.cu:543`

So FlashMask-on-FA3 does not reuse the generic FA3 kernel wrapper. It uses a separate op and a separate dynload ABI.

Core forward file:

- `Paddle/paddle/phi/kernels/gpu/flash_attn_v3_kernel.cu`

Core backward file:

- `Paddle/paddle/phi/kernels/gpu/flash_attn_v3_grad_kernel.cu`

Handle-building helpers:

- `Paddle/paddle/phi/kernels/gpu/flash_attn_v3_utils.cu`

Dynload ABI:

- `Paddle/paddle/phi/backends/dynload/flashmaskv2.h`

### Forward

Forward wrapper:

- `FlashMaskV2Kernel(...)` at `Paddle/paddle/phi/kernels/gpu/flash_attn_v3_kernel.cu:2293`

Main implementation:

- `FlashMaskV2BaseKernel(...)` at `Paddle/paddle/phi/kernels/gpu/flash_attn_v3_kernel.cu:1186`

Flow:

1. Build an opaque FlashMask-v2 forward params handle.
2. Slice `startend_row_indices` into `lt_start`, `lt_end`, `ut_start`, `ut_end`.
3. Allocate and populate `flashmask_maxmin`.
4. Optionally attach `block_mask`.
5. Optionally attach overlap metadata `unique_id`, `rank`, `nranks`.
6. Launch `dynload::flashmaskv2_run_mha_fwd(...)`.

Key lines:

- handle setup call: `Paddle/paddle/phi/kernels/gpu/flash_attn_v3_kernel.cu:1571`
- interval slicing: `Paddle/paddle/phi/kernels/gpu/flash_attn_v3_kernel.cu:2044`
- set interval pointers on handle: `Paddle/paddle/phi/kernels/gpu/flash_attn_v3_kernel.cu:2161`
- launch: `Paddle/paddle/phi/kernels/gpu/flash_attn_v3_kernel.cu:2236`

### Backward

Backward wrapper:

- `FlashMaskV2GradKernel(...)` at `Paddle/paddle/phi/kernels/gpu/flash_attn_v3_grad_kernel.cu:1585`

Main implementation:

- `FlashMaskV2GradBaseKernel(...)` at `Paddle/paddle/phi/kernels/gpu/flash_attn_v3_grad_kernel.cu:794`

Flow mirrors forward:

1. Build backward params handle.
2. Re-slice `startend_row_indices`.
3. Rebuild `flashmask_maxmin`.
4. Attach interval pointers and optional `block_mask`.
5. Attach overlap metadata.
6. Launch `dynload::flashmaskv2_run_mha_bwd(...)`.

Key lines:

- handle setup call: `Paddle/paddle/phi/kernels/gpu/flash_attn_v3_grad_kernel.cu:1399`
- interval slicing: `Paddle/paddle/phi/kernels/gpu/flash_attn_v3_grad_kernel.cu:1001`
- set interval pointers on handle: `Paddle/paddle/phi/kernels/gpu/flash_attn_v3_grad_kernel.cu:1488`
- launch: `Paddle/paddle/phi/kernels/gpu/flash_attn_v3_grad_kernel.cu:1560`

### FA3 registration

- forward kernel registration: `Paddle/paddle/phi/kernels/gpu/flash_attn_v3_kernel.cu:2416`
- backward kernel registration: `Paddle/paddle/phi/kernels/gpu/flash_attn_v3_grad_kernel.cu:1700`

## FA2 vs FA3: Practical Difference

FA2:

- op name: `flashmask_attention`
- sparse mask ABI is passed as raw pointer arguments directly into `flash_attn_fwd` / `flash_attn_bwd`
- still uses the older dropout / RNG / seed-offset path
- no `block_mask`
- no explicit overlap `unique_id/rank/nranks`

FA3:

- op name: `flashmask_attention_v2`
- uses a dedicated shared library `libflashmaskv2.so`
- sparse mask ABI is attached through `flashmaskv2_*_params_set_*` setters on an opaque handle
- supports `block_mask`
- supports distributed overlap metadata through `unique_id`, `rank`, `nranks`
- requires a separate public dispatch path because generic FA3 rejects FlashMask

## Stripped Call Graph

If you only want the smallest useful read order, use this:

### Common path

1. `PaddleNLP/paddlenlp/transformers/llama/fusion_ops.py`
2. `Paddle/python/paddle/nn/functional/flash_attention.py`
3. `Paddle/paddle/phi/ops/yaml/ops.yaml`

### If you want to see where the sparse mask comes from

4. `PaddleNLP/llm/utils/data.py`
5. `PaddleNLP/paddlenlp/datasets/zero_padding_dataset.py`
6. `PaddleNLP/paddlenlp/transformers/tokenizer_utils_base.py`

### FA2 branch

7. `Paddle/paddle/phi/kernels/gpu/flash_attn_utils.h`
8. `Paddle/paddle/phi/kernels/gpu/flash_attn_kernel.cu`
9. `Paddle/paddle/phi/kernels/gpu/flash_attn_grad_kernel.cu`

### FA3 branch

10. `Paddle/paddle/phi/kernels/gpu/flash_attn_v3_utils.cu`
11. `Paddle/paddle/phi/backends/dynload/flashmaskv2.h`
12. `Paddle/paddle/phi/kernels/gpu/flash_attn_v3_kernel.cu`
13. `Paddle/paddle/phi/kernels/gpu/flash_attn_v3_grad_kernel.cu`

### Optional representative PaddleNLP model wrappers

- `PaddleNLP/paddlenlp/transformers/llama/modeling.py`
- `PaddleNLP/paddlenlp/transformers/qwen2/modeling.py`
- `PaddleNLP/paddlenlp/transformers/deepseek_v2/modeling_auto.py`

## Minimal Mental Model

The essential seam is:

`PaddleNLP sparse mask builder`
-> `fusion_ops.py`
-> `F.flashmask_attention(...)`
-> `flashmask_attention` or `flashmask_attention_v2`
-> slice `startend_row_indices` into lower/upper interval tensors
-> attach `flashmask_maxmin`
-> launch FA2 or FA3 with FlashMask metadata

That is the implementation interface you care about.

## Second-Pass: Tests, Docs, And Reference Material

The first pass focused on runtime code. A second pass over files and folders with `flashmask` in their names turned up a useful supporting layer that should also be in scope when studying or porting the idea.

### Best semantic reference files

If you want the cleanest executable explanation of what the interval representation means, read these before the kernels:

- `Paddle/test/test_flashmask_ci/generate_startend_row_indices.py`
- `Paddle/test/legacy_test/test_flashmask.py`
- `Paddle/test/xpu/test_flashmask_attention_op_xpu.py`

Why these matter:

- they reconstruct dense masks from `startend_row_indices`
- they generate concrete interval masks for multiple structured patterns
- they show what Paddle considers valid semantic behavior for the representation

The most useful helper is:

- `startend_row_indices_to_attn_bias(...)` in `Paddle/test/test_flashmask_ci/generate_startend_row_indices.py:17`

This function is the clearest executable definition of how Paddle interprets `startend_row_indices`.

One subtle but important behavior appears there:

- when `causal=True` and `seqlen_q != seqlen_k`, causal masking is aligned to the bottom-right corner, matching FlashAttention 2.1+ / FlashAttention 3 behavior

Several generator functions in that file are also directly relevant to structured-mask adoption:

- `generate_sliding_window_mask`
- `generate_causal_document_mask`
- `generate_document_mask`
- `generate_share_question_mask`
- `generate_global_sliding_window_mask`
- `generate_causal_blockwise_mask`
- `generate_prefix_lm_document_mask`
- `generate_prefix_lm_causal_mask`

Note that some of the document-style generators are marked with `TODO: this seems buggy`, so they should be treated as useful examples rather than canonical truth.

### CI harness around FlashMask

The directory:

- `Paddle/test/test_flashmask_ci/`

is a focused FlashMask validation harness.

Relevant files there:

- `generate_startend_row_indices.py`
- `test_flashmask_ci.py`
- `test_flashmask_group.py`
- `test_fwd_md5sum.py`
- `test_util.py`
- `flashmask_gt.json`
- `run.sh`

What each one contributes:

- `test_flashmask_ci.py`
  - broad numerical parity test against a reference implementation
  - exercises many mask families and shape combinations
  - currently parameterized for `fa_version=3` and `dtype=bfloat16`
  - uses `startend_row_indices_to_attn_bias(...)` plus `attention_ref(...)` to compare forward and backward numerics

- `test_fwd_md5sum.py`
  - regression test for deterministic forward outputs
  - computes output md5sums over many parameter combinations
  - compares against `flashmask_gt.json`
  - this is useful because it treats FlashMask as a stable observable interface, not just a kernel implementation detail

- `flashmask_gt.json`
  - stored expected hashes for many forward test combinations
  - effectively a golden-output ledger for the CI suite

- `test_flashmask_group.py`
  - validates the `group` / distributed-overlap plumbing in the Python API
  - covers `_flashmask_unique_id_cache`, `_get_or_create_unique_id(...)`, and mask-shape validation under `nranks > 1`

- `test_util.py`
  - contains `attention_ref(...)`, the dense reference attention used for CI comparisons
  - useful if you want a simple correctness oracle while prototyping in another stack

- `run.sh`
  - shows the intended CI invocation order:
    - `test_flashmask_ci.py`
    - `test_fwd_md5sum.py`
    - `test_flashmask_group.py`

### Legacy and backend-specific tests

- `Paddle/test/legacy_test/test_flashmask.py`
  - older CUDA-facing parity tests
  - includes a `flashmask_to_densemask(...)` helper
  - compares `flashmask_attention(...)` against naive dense attention
  - covers several mask families, dtype combinations, broadcasting cases, and zero-size tensor behavior

- `Paddle/test/legacy_test/test_flashmask_unique_id.py`
  - minimal API-level test for `flashmask_get_unique_id()`
  - checks CPU placement, `uint8` dtype, and `128`-byte size

- `Paddle/test/xpu/test_flashmask_attention_op_xpu.py`
  - XPU backend parity tests
  - covers causal mask, shared-question mask, and causal blockwise mask
  - confirms that the interval-mask representation is meant to be backend-agnostic at the API level, even if kernel internals differ

### User-facing documentation

There are several layers of user-facing documentation:

- `FlashMask/summary_flashmask.md`
  - concise paper/application summary
  - useful for adoption framing

- `FlashMask/documentation/02-flashmask-innovation.md`
  - the cleanest prose explanation of the column-wise sparse representation and block-skip idea

- `PaddleNLP/llm/docs/flashmask.md`
  - main PaddleNLP integration doc in Chinese
  - combines paper motivation, implementation explanation, and quick-start commands

- `PaddleNLP/docs/en/llm/docs/flashmask.md`
  - English version of the above

- `PaddleNLP/docs/en/llm/alignment/rm/flashmask/README.md`
  - specific Reward Model training walkthrough for FlashMask

These docs matter because they expose the intended workload classes:

- SFT
- LoRA
- DPO
- RM
- packed/document-style masking
- shared-question layouts
- prefix-style masking

### Example configs and fixtures

FlashMask also has explicit config examples and test fixtures:

- `PaddleNLP/llm/config/llama/flashmask/dpo.json`
- `PaddleNLP/llm/config/llama/flashmask/lora.json`
- `PaddleNLP/llm/config/llama/flashmask/rm.json`
- `PaddleNLP/llm/config/llama/flashmask/sft.json`
- `PaddleNLP/llm/config/llama/prm_flashmask_argument.json`
- `PaddleNLP/llm/config/llama/rm_flashmask_argument.json`
- `PaddleNLP/llm/config/mistral/prm_flashmask_argument.json`
- `PaddleNLP/tests/fixtures/llm/prm_flashmask.yaml`
- `PaddleNLP/tests/fixtures/llm/rm_flashmask.yaml`

What these show:

- FlashMask is expected to be enabled together with `use_flash_attention`
- most training examples also pair it with `zero_padding` / `greedy_zero_padding`
- the main intended use is not arbitrary masking but structured training layouts

### Miscellaneous `flashmask`-named code

- `PaddleNLP/ops/csrc/paddle_bwd_ops/flashmask_attn_bwd.cc`

This file is not part of the main Paddle kernel/runtime path described above. It is a small extension wrapper around `paddle::experimental::flashmask_attention_grad(...)`, useful mostly as evidence that there is or was extension-level exposure of the backward op.

## Revised Read Order

If you want the best study order now that the second pass is included, use:

1. `FlashMask/documentation/02-flashmask-innovation.md`
2. `Paddle/test/test_flashmask_ci/generate_startend_row_indices.py`
3. `PaddleNLP/paddlenlp/transformers/llama/fusion_ops.py`
4. `Paddle/python/paddle/nn/functional/flash_attention.py`
5. `Paddle/paddle/phi/ops/yaml/ops.yaml`
6. `Paddle/paddle/phi/kernels/gpu/flash_attn_utils.h`
7. `Paddle/paddle/phi/kernels/gpu/flash_attn_kernel.cu`
8. `Paddle/paddle/phi/kernels/gpu/flash_attn_grad_kernel.cu`
9. `Paddle/paddle/phi/kernels/gpu/flash_attn_v3_utils.cu`
10. `Paddle/paddle/phi/backends/dynload/flashmaskv2.h`
11. `Paddle/paddle/phi/kernels/gpu/flash_attn_v3_kernel.cu`
12. `Paddle/paddle/phi/kernels/gpu/flash_attn_v3_grad_kernel.cu`
13. `Paddle/test/test_flashmask_ci/test_flashmask_ci.py`
14. `Paddle/test/test_flashmask_ci/test_flashmask_group.py`

If you only want the shortest porting-oriented path for another project, use:

1. `FlashMask/documentation/02-flashmask-innovation.md`
2. `Paddle/test/test_flashmask_ci/generate_startend_row_indices.py`
3. `Paddle/python/paddle/nn/functional/flash_attention.py`
4. `Paddle/paddle/phi/kernels/gpu/flash_attn_kernel.cu`
5. `Paddle/paddle/phi/kernels/gpu/flash_attn_v3_kernel.cu`

That gives:

- paper-level representation
- executable interval semantics
- public API contract
- FA2 ABI
- FA3 ABI
