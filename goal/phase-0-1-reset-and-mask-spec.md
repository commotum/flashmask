# Phase 0-1: Reset And Mask Spec

## Pasteable Goal

Reset the current FlashMask/PE work around the standalone-kernel architecture,
then lock down and test the interval-mask specification before more kernel or PE
integration work. See
`/home/jake/Developer/flashmask/goal/phase-0-1-reset-and-mask-spec.md` for the
detailed scope, tests, and exit criteria.

## Objective

Reset the project around the corrected architecture and lock down the standalone
mask specification before more kernel or PE integration work.

This phase is intentionally front-loaded. The kernel port should not proceed
until the project has a precise, tested definition of what mask metadata means
and which current worktree changes are useful.

## Non-Goals

- Do not optimize kernels in this phase.
- Do not expand PE integration beyond preserving or repairing a minimal
  fail-closed adapter.
- Do not benchmark speed as proof of completion.
- Do not introduce Paddle or PaddleNLP as runtime dependencies.

## Worktree Reset Audit

Produce a short audit of the current `flashmask` and `pe` worktrees before
continuing implementation.

Classify existing changes as:

- Keep: pure mask representation, dense reconstruction, PE metadata compilers,
  and tests that directly prove mask semantics.
- Rework: API or backend routing scaffolding that is useful but depends on
  unfinished kernel assumptions.
- Remove or quarantine: benchmark/proof scaffolding that implies the kernel is
  complete when it is not.
- Defer: PE training, rollout benchmarks, and proof artifacts that require real
  forward/backward kernels.

The output should identify the exact files in each category and the reason for
the classification.

## Reference Map

Document how the reference implementation maps into the standalone package.

Minimum references:

- `sub/Paddle/test/test_flashmask_ci/generate_startend_row_indices.py`
  - executable definition of `startend_row_indices` dense semantics.
- `sub/Paddle/python/paddle/nn/functional/flash_attention.py`
  - public FlashMask API shape, FA2/FA3 dispatch, and argument validation.
- `sub/Paddle/paddle/phi/kernels/gpu/flash_attn_kernel.cu`
  - FA2-era interval pointer slicing and `flashmask_maxmin` handoff.
- `sub/Paddle/paddle/phi/kernels/gpu/flash_attn_v3_kernel.cu`
  - FA3-era FlashMask v2 path, param handle population, `block_mask`, and
    `flashmaskv2_run_mha_fwd`.
- `sub/Paddle/paddle/phi/kernels/gpu/flash_attn_v3_grad_kernel.cu`
  - FA3-era backward reference path.
- `sub/PaddleNLP/paddlenlp/transformers/llama/fusion_ops.py`
  - downstream handoff pattern from model code into FlashMask.
- `context/masks.py`
  - structured masks the standalone representation should support.

The map should say what is copied/adapted, what is only conceptual reference,
and what must be replaced because it is framework-specific.

## Runtime Boundary

`flashmask` owns:

- interval-mask representation
- mask compilers
- dense-reference reconstruction for tests
- backend routing metadata
- PyTorch extension ABI
- kernel-native attention interface

`pe` owns:

- experiment policy
- tokenization
- positional encodings
- batching
- torch tensor assembly
- model code
- training and evaluation

`ankos` owns:

- cellular automata mechanics
- rollout generation
- NumPy-style raw episode outputs

No `flashmask` runtime import may import Paddle or PaddleNLP.

## IntervalMask Spec

The core sparse mask tensor shape is:

```text
[batch, mask_heads, seqlen_k, bound_num]
```

where `bound_num` is one of `{1, 2, 4}`.

The representation is column-wise: for each key column `k`, the metadata
describes query-row intervals that are masked. Dense reference helpers should
return PE/PyTorch-style boolean visibility masks:

```text
[batch, heads, seqlen_q, seqlen_k]
True  = query may attend to key
False = query may not attend to key
```

The interval metadata itself follows the FlashMask convention of describing
masked row spans. All intervals are half-open: `[start, end)`.

### Causal Mode

When `causal=True`:

- `bound_num == 1`
  - `lts = startend[..., 0]`
  - masks lower-triangle rows `[lts, seqlen_q)` for each key column
- `bound_num == 2`
  - `lts = startend[..., 0]`
  - `lte = startend[..., 1]`
  - masks lower-triangle rows `[lts, lte)` for each key column

The dense reference must also apply FlashAttention's bottom-right causal
alignment behavior when `seqlen_q != seqlen_k`.

### Non-Causal Mode

When `causal=False`:

- `bound_num == 2`
  - `lts = startend[..., 0]`
  - `ute = startend[..., 1]`
  - masks lower rows `[lts, seqlen_q)` and upper rows `[0, ute)`
- `bound_num == 4`
  - `lts = startend[..., 0]`
  - `lte = startend[..., 1]`
  - `uts = startend[..., 2]`
  - `ute = startend[..., 3]`
  - masks lower rows `[lts, lte)` and upper rows `[uts, ute)`

This is the form expected to represent PE's state-autoregressive visibility,
because PE visibility is timestep-causal rather than flat-token causal.

## PE State-Autoregressive Semantics

The PE dense reference is authoritative for this phase.

Given query metadata:

- `query_time_index: [B, Q]`
- `query_token_type: [B, Q]`

and key metadata:

- `key_time_index: [B, K]`
- `key_token_type: [B, K]`
- `key_valid_token: [B, K]`

the visibility rule is:

- State query token:
  - may attend to BOS and domain keys
  - may attend to state keys with `key_time <= query_time`
  - may not attend to future state keys
- BOS query token:
  - may attend only to BOS key
- Domain query token:
  - may attend to BOS and domain keys
- Any query:
  - may not attend to invalid/padding keys

The compiler must preserve same-timestep state visibility. That means state
tokens at time `t` may attend to other state tokens at time `t`, because PE is
predicting next state from a timestep-causal state context, not enforcing
strict causal order over flattened tokens.

Loss targeting remains PE-owned. FlashMask only controls attention visibility.

## Full-Sequence And Query Masks

The full-sequence compiler handles:

```text
query metadata == key metadata
```

The query/incremental compiler handles cached rollout:

```text
query metadata = current decoded block
key metadata   = accumulated cache metadata + current decoded block
```

Both compilers must produce metadata whose dense reconstruction exactly matches
the PE dense reference.

## Structured Mask Families

Represent masks from `context/masks.py` when they can be expressed as per-key
query-row intervals:

- causal
- sliding-window
- document
- causal document
- prefix-LM
- blockwise
- global-token plus sliding-window
- QK-sparse
- random eviction and related interval variants

For each family, tests should cover:

- valid construction
- dense reconstruction
- head broadcasting
- batch handling
- invalid or non-representable masks

If a mask cannot be represented as contiguous per-key row intervals, the
compiler should fail with a clear `MaskNotRepresentableError` or equivalent.

## API Surface To Stabilize

The exact names can change if the codebase already established better names,
but this phase should stabilize the concepts:

- `IntervalMask`
- `IntervalMask.to_bool_mask(...)`
- PE full-sequence compiler
- PE query/incremental compiler
- structured mask constructors
- dense reference helpers
- `MaskNotRepresentableError`

The API should be usable without importing torch when only pure Python dense
reference data is requested, except where tensor inputs make torch unavoidable.

## Test Requirements

Run CPU-safe tests with `uv`.

Required test coverage:

- no Paddle/PaddleNLP runtime imports
- `IntervalMask` shape validation
- `bound_num` validation
- causal dense reconstruction
- non-causal dense reconstruction
- PE full-sequence compiler parity against PE dense reference
- PE query/incremental compiler parity against PE dense reference
- padding exclusion
- same-timestep state visibility
- special-token visibility
- mask-head broadcasting to model heads
- structured masks from `context/masks.py`
- non-representable mask failures

Representative commands:

```bash
uv run pytest -q
```

From PE, where applicable:

```bash
PYTHONPATH=/home/jake/Developer/flashmask/src uv run pytest -q tests/test_batch.py tests/test_attention.py
```

## Exit Evidence

This phase is complete only when the following evidence exists:

- A current worktree audit identifies what was kept, reworked, removed, and
  deferred.
- A reference map links the relevant Paddle/PaddleNLP files to standalone
  FlashMask responsibilities.
- Pure Python mask tests pass.
- PE mask parity tests pass.
- The package imports without Paddle/PaddleNLP.
- No benchmark or proof document claims kernel speed before the kernel is real.
- Phase 2 can start from a stable mask spec and a clear PyTorch extension ABI
  target.
