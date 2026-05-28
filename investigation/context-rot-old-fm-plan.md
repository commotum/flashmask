# Standalone FlashMask Plan for `nanochat` / `pe`

Compiled on `2026-04-15`.

## Decision

- Build one **FlashMask-style standalone module** for the `pe` repo.
- Do **not** build parallel FlexAttention and FlashMask implementations for the benchmark.
- Use the standalone module behind one clean API, with:
  - a **reference backend** for correctness and tests
  - one **optimized sparse backend** only if the reference path becomes the bottleneck
- Keep `0D+t` on the ordinary token-causal path.
- Move `1D+t`, `2D+t`, and `3D+t` to **next-state prediction** with timestep-causal visibility.

## Why This Is Feasible

- Attention is already centralized enough in the current repo.
  - [nanochat/gpt.py](/home/jake/Developer/pe/nanochat/gpt.py:104) has the single training-time attention call.
  - [nanochat/flash_attention.py](/home/jake/Developer/pe/nanochat/flash_attention.py:107) already isolates the ordinary FA3 vs SDPA dispatch.
- Positional encoding is separate from masking.
  - [nanochat/gpt.py](/home/jake/Developer/pe/nanochat/gpt.py:97) applies RoPE before the attention call.
  - [PE/pe.py](/home/jake/Developer/pe/PE/pe.py:35) contains MonSTER math that does not assume a specific attention mask.
- The benchmark goal is already aligned with state-autoregressive prediction.
  - [The-Goal.md](/home/jake/Developer/fm/The-Goal.md:3)
  - [README.md](/home/jake/Developer/pe/README.md:3)
- The main obstacle is not the model math. It is the current training/data factorization.
  - [nanochat/dataloader.py](/home/jake/Developer/pe/nanochat/dataloader.py:154) and [nanochat/dataloader.py](/home/jake/Developer/pe/nanochat/dataloader.py:155) still hardwire shift-by-1 next-token targets.

## Core Recommendation

Implement a narrow, benchmark-specific FlashMask path instead of a general arbitrary-mask system.

The standalone module should target exactly this mask family:

- serialization is **time-major**
- each spatial timestep is one fixed-width state block
- attention is **timestep-causal**
- within a timestep, all tokens may attend to all tokens in that timestep
- targets are shifted by **one full state**, not by one token

That is enough for your benchmark and avoids overbuilding.

## Scope

### In Scope

- CUDA-first training path for spatial tasks
- `q`, `k`, `v` in current nanochat layout: `(B, T, H, D)`
- GQA-compatible shapes, matching current `nanochat/gpt.py`
- `startend_row_indices` generation for one regular timestep-block mask family
- benchmark-native dataloader and rollout loop
- spatial task support for:
  - `1D+t`
  - `2D+t`
  - `3D+t`

### Out of Scope for V1

- literal port of Paddle’s full FlashMask implementation
- Paddle op/YAML integration
- distributed-overlap / unique-id support
- XPU support
- KV-cache integration for token-at-a-time decoding
- generic arbitrary sparse masks
- sliding-window plus FlashMask combinations
- chat/SFT/RL integration

## Design Constraints From Current Code

- The current model always uses token-causal attention in training.
  - [nanochat/gpt.py](/home/jake/Developer/pe/nanochat/gpt.py:108)
- The current attention wrapper only understands ordinary causal and sliding-window patterns.
  - [nanochat/flash_attention.py](/home/jake/Developer/pe/nanochat/flash_attention.py:69)
- The current training loss is plain cross-entropy against shift-by-1 targets.
  - [nanochat/gpt.py](/home/jake/Developer/pe/nanochat/gpt.py:474)
- The current dataloader is text-specific and should not be reused for the benchmark.
  - [nanochat/dataloader.py](/home/jake/Developer/pe/nanochat/dataloader.py:74)
- MonSTER does **not** require `head_dim=128`.
  - It requires `head_dim % 12 == 0` in the current scratch implementation.
  - [PE/pe.py](/home/jake/Developer/pe/PE/pe.py:38)
  - [PE/Head-Construction.md](/home/jake/Developer/pe/PE/Head-Construction.md:18)

## Recommended Architecture

### 1. Add a New Standalone Module

Add:

- [nanochat/flashmask.py](/home/jake/Developer/pe/nanochat)

This module should be independent from the existing FA3 wrapper and expose one benchmark-facing API.

Recommended API:

```python
def flashmask_attention(
    q,
    k,
    v,
    startend_row_indices,
    *,
    causal: bool = False,
    backend: str = "auto",
):
    ...

def build_timeblock_startend_row_indices(
    *,
    seq_len: int,
    tokens_per_state: int,
    prefix_len: int,
    mode: str = "same_state_visible",
    device=None,
):
    ...
```

Recommended backends:

- `ref`: dense reference implementation using ordinary PyTorch ops
- `sparse`: optimized implementation for the same semantics
- `auto`: choose `sparse` when available, otherwise `ref`

This keeps the user-facing API to **one FlashMask module**, not two competing attention systems.

### 2. Keep `nanochat/flash_attention.py` Mostly Untouched

Do not overload the existing FA3 wrapper with benchmark-only semantics.

Instead:

- keep [nanochat/flash_attention.py](/home/jake/Developer/pe/nanochat/flash_attention.py:107) as the ordinary causal/sliding-window path
- call the new `nanochat.flashmask` module directly from the benchmark path

That preserves the current text model behavior and keeps the sparse benchmark path easy to reason about.

### 3. Add a Benchmark Data Path

Do not reuse:

- [nanochat/dataloader.py](/home/jake/Developer/pe/nanochat/dataloader.py)
- [scripts/base_train.py](/home/jake/Developer/pe/scripts/base_train.py)

Add a benchmark-native stack instead:

- `nanochat/pe/tasks.py`
- `nanochat/pe/serialize.py`
- `nanochat/pe/data.py`
- `nanochat/pe/metrics.py`
- `scripts/pe_train.py`
- `scripts/pe_eval.py`

This path should emit:

- `input_ids`
- `targets`
- `coords`
- `loss_mask`
- `flashmask_indices`
- metadata like `task_id`, `rule_id`, `seed`, and `tokens_per_state`

## Key Modeling Choice

### Spatial Tasks Must Use Next-State Targets

For `1D+t`, `2D+t`, and `3D+t`:

- do **not** use `target = token[i+1]`
- use `target = same_position_in_next_state`

That means:

- timestep `t` may see all tokens in timesteps `<= t`
- same-state full visibility is valid
- there is no future-token leakage inside the current state block

For `0D+t`:

- keep the ordinary token-causal setup
- no FlashMask path is required

## State Width Recommendation

Pad every spatial timestep to **128 tokens**.

Recommended padded sizes:

- `1D+t`: `123 -> 128`
- `2D+t`: `121 -> 128`
- `3D+t`: `125 -> 128`

Why:

- cleaner timestep-block boundaries
- easier mask construction
- easier statewise rollout
- future optimized sparse kernels can assume aligned state blocks

Implementation detail:

- padded dummy tokens should get `target = -1`
- padded dummy positions should be excluded from the benchmark metrics

## Minimal Integration Into the Current Model

### `nanochat/gpt.py`

Modify the attention path minimally.

Recommended changes:

- extend `CausalSelfAttention.forward(...)` to accept an optional attention spec
- extend `Block.forward(...)` to thread that spec through
- extend `GPT.forward(...)` to accept optional:
  - `coords=None`
  - `attn_spec=None`
  - `loss_mask=None`

Recommended behavior:

- if `attn_spec is None`, keep the current causal FA3/SDPA path
- if `attn_spec.kind == "flashmask"`, call `nanochat.flashmask.flashmask_attention(...)`

This keeps the model fork small.

### `PE/pe.py` and PE Integration

Do not couple PE math to the mask implementation.

The sparse backend should not know or care whether the model is using:

- flat RoPE
- axial RoPE
- 4D axial RoPE
- MonSTER

The only contract is:

- PE transforms `q` and `k`
- FlashMask controls visibility

## Concrete Patch Set

### New Files

- `/home/jake/Developer/pe/nanochat/flashmask.py`
- `/home/jake/Developer/pe/nanochat/pe/__init__.py`
- `/home/jake/Developer/pe/nanochat/pe/tasks.py`
- `/home/jake/Developer/pe/nanochat/pe/serialize.py`
- `/home/jake/Developer/pe/nanochat/pe/data.py`
- `/home/jake/Developer/pe/nanochat/pe/metrics.py`
- `/home/jake/Developer/pe/scripts/pe_train.py`
- `/home/jake/Developer/pe/scripts/pe_eval.py`
- `/home/jake/Developer/pe/tests/test_flashmask_semantics.py`
- `/home/jake/Developer/pe/tests/test_pe_data.py`

### Modified Files

- `/home/jake/Developer/pe/nanochat/gpt.py`
- optionally `/home/jake/Developer/pe/nanochat/common.py` for artifact-path helpers
- optionally `/home/jake/Developer/pe/nanochat/report.py` if benchmark reports should reuse the existing report format

## Phase Plan

### Phase 1: Freeze Semantics

- Freeze the exact timestep visibility policy.
- Freeze the prefix convention, likely `BOS + TASK_ID`.
- Freeze the padded `tokens_per_state` for each spatial task.
- Freeze whether same-state visibility means `k_t <= q_t` or strict `k_t < q_t`.

Deliverable:

- one short spec doc or comment block in `nanochat/flashmask.py`

### Phase 2: Build the Benchmark Batch API

- implement procedural generators for the four task families from [V1.md](/home/jake/Developer/fm/V1.md)
- serialize in time-major order
- generate `coords[..., 4]` for `(t, x, y, z)`
- build next-state targets for spatial tasks
- emit `loss_mask`
- emit `flashmask_indices`

Deliverable:

- a batch object that can drive the model without any text-tokenizer code

### Phase 3: Add the Reference FlashMask Backend

- implement `build_timeblock_startend_row_indices(...)`
- implement a dense reference `flashmask_attention(...)`
- make it work on CPU and CUDA
- use it in training first

This is the correctness milestone.

Deliverable:

- end-to-end training for at least one spatial task with benchmark-correct semantics

### Phase 4: Wire the Model

- thread `attn_spec` through [nanochat/gpt.py](/home/jake/Developer/pe/nanochat/gpt.py)
- preserve the current causal path for all existing text workflows
- only enable FlashMask in the benchmark scripts

Deliverable:

- existing nanochat text flows unchanged
- benchmark flow can toggle `attention_mode="flashmask"`

### Phase 5: Add a Statewise Evaluation Loop

- do not use token-at-a-time chat generation for the benchmark
- roll out one full state at a time
- append the predicted state block
- recompute or extend the FlashMask indices per step as needed

Deliverable:

- `scripts/pe_eval.py` with prompt states -> rollout states evaluation

### Phase 6: Optimize Only If Needed

- profile the reference backend first
- if it is not the bottleneck at your benchmark sizes, stop here
- if it is the bottleneck, implement one optimized sparse backend inside `nanochat/flashmask.py`

Important constraint:

- optimize the same API and the same semantics
- do **not** add a second user-facing attention path

## Testing Plan

### Required Tests

- `flashmask_attention(ref)` matches dense masked attention numerically on toy cases
- `build_timeblock_startend_row_indices(...)` produces the expected mask for:
  - prefix tokens
  - first timestep
  - middle timestep
  - final timestep
- spatial targets are shifted by exactly one full state
- padded dummy tokens are ignored in loss and metrics
- `0D+t` still runs on the ordinary causal path
- text-model training paths still behave unchanged

### Useful Smoke Tests

- `1D+t` one-batch forward/backward on CPU
- `2D+t` one-batch forward/backward on CUDA
- one end-to-end rollout for `3D+t`
- PE parity test: all PE variants can run against the same FlashMask attention spec

## Main Risks

- Trying to optimize too early
- mixing the benchmark path into the existing text dataloader
- keeping shift-by-1 targets while also enabling same-state visibility
- binding the sparse backend to `head_dim=128` when MonSTER wants `head_dim % 12 == 0`
- modifying `nanochat/flash_attention.py` so heavily that existing text behavior becomes harder to trust

## Recommended First Milestone

The first milestone should be:

- `1D+t` only
- padded to 128 tokens per state
- next-state targets
- reference FlashMask backend only
- one PE method first, preferably flat RoPE

Success criteria:

- one training run works end to end
- same-state visibility is enabled without leakage
- loss and rollout metrics are computed on the correct target window

Only after that should you add:

- other PE variants
- `2D+t` and `3D+t`
- the optimized sparse backend

## Bottom Line

This is doable, but the straight path is:

1. build **one** standalone FlashMask-style module
2. switch the spatial tasks to **next-state** training
3. add a benchmark-native data/eval path
4. integrate the module into `nanochat/gpt.py` with a small optional branch
5. optimize later, not first

That gives you the lightweight experimental stack you want without paying for both a FlexAttention branch and a FlashMask branch.
