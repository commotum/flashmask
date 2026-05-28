Yes. If you want to treat ARC more like **next-state prediction over a sequence of grids** than next-token prediction over one flat stream, the pipeline becomes much cleaner.

I would structure it like this.

**1. Represent each ARC task as a temporal episode**

For a task with demonstrations plus one query:

```text
demo_1_input -> demo_1_output
demo_2_input -> demo_2_output
...
demo_n_input -> demo_n_output
query_input  -> query_output
```

At training time, you can turn this into multiple supervised steps:
- predict `demo_1_output` from `demo_1_input`
- predict `demo_2_output` from `demo_1_input, demo_1_output, demo_2_input`
- ...
- predict `query_output` from all demos plus `query_input`

That gives you “next-grid” supervision, not next-token supervision.

**2. Build each training step as a packed sequence of segments**

For one prediction step, pack only the active cells contiguously:

```text
[header demo_1_in][demo_1_in cells]
[header demo_1_out][demo_1_out cells]
...
[header current_in][current_in cells]
[header target_out][target_out slots]
```

Key point:
- all earlier known grids are packed as observed segments
- the target output is packed as **blank slots**, one per output cell
- labels are stored separately for those target slots

No global `30x30` padding is needed in the canonical representation.

**3. Emit explicit metadata for every token**

For every token, data prep should emit tensors like:

- `segment_id`
- `time_idx`
- `role_id`
  - `demo_input`
  - `demo_output`
  - `query_input`
  - `query_output_slot`
  - maybe `header`
- `grid_id`
- `row`
- `col`
- `grid_height`
- `grid_width`
- `is_observed`
- `is_target_slot`
- `loss_mask`
- `valid_token`

So yes, token position becomes explicit, not implicit.

**4. Positional encoding uses local geometry, not packed index**

The packed index is just storage order.

Position should come from:
- local `row`
- local `col`
- maybe `role_id`
- maybe `time_idx`

So a token’s meaning is based on:
- where it is in its own grid
- what grid/segment it belongs to
- where that segment sits in the episode timeline

Not on “I happen to be token 1372 in a giant flattened canvas.”

**5. FlashMask compiles from metadata, not raw tokens**

The API should look like:

```python
compiled = compile_mask(layout, spec="temporal_arc_step", backend="dense")
out = flashmask_attention(q, k, v, compiled)
```

Where `layout` is the packed sequence plus metadata.

For the ARC step case, the mask semantics are:

- all observed prefix segments can interact freely
- target output slots can attend to all observed prefix segments
- target output slots can attend to each other within the same output grid
- nothing should attend across different packed sequences in the batch
- pad/header handling is explicit

That is already much more structured than HRM’s “everything dense over a padded canvas.”

**6. Dense baseline first**

For Phase 1.1, the dense compiler is straightforward:

```python
allow(q, k) =
    same_sequence(q, k)
    and valid(k)
    and (
        both_in_observed_prefix(q, k)
        or (is_target_slot(q) and in_observed_prefix(k))
        or (same_target_grid(q, k))
    )
```

Then:
- build a dense boolean mask
- run reference attention
- verify outputs and grads

**7. Column-interval FlashMask second**

Because segments are contiguous, the mask becomes much more interval-friendly.

For a typical packed step:
- observed prefix is one contiguous region
- target slots are one contiguous region

So for many keys:
- keys in observed prefix are visible to all observed + target queries
- keys in target block are visible only to target queries

That produces simple blocked visibility patterns, much closer to FlashMask’s intended representation than HRM’s random placement inside `30x30`.

**8. Loss is only on target slots**

This is important.

For each packed step:
- observed segments are context only
- target output slots are scored
- everything else is `ignore_index`

So the loss path is:

```python
labels[~loss_mask] = ignore_index
```

That makes the task “predict the next grid state,” not “reconstruct every token in the packed sequence.”

**9. Variable-size outputs**

This is the one hard part you still have to handle explicitly.

At training time, output size is known.
At inference time for ARC, `query_output` size is not known in general.

So I would split inference into two stages:
- first predict output geometry `(H_out, W_out)`
- then instantiate `H_out * W_out` target slots and predict cell values

That keeps the packed representation compact and avoids falling back to full `30x30` padding.

**10. The actual integrated pipeline**

So end to end, I’d do:

1. Raw ARC task
2. Convert to episode of grid transitions
3. For each prediction step:
   - pack observed grids contiguously
   - append target output slots contiguously
   - emit token metadata
4. Apply local 2D position encoding from metadata
5. Compile dense or interval FlashMask from metadata
6. Run attention backend
7. Score only target slots

That is the clean version of “data prep and FlashMask working in concert.”

If you want, I can write this as a concrete schema next:
- `EpisodeStep`
- `PackedSequence`
- `TokenMetadata`
- `compile_mask(layout, spec)`
