# GPU Tips for Packed Active Cells

## Short answer

Packing only the active cells is usually a good idea when occupancy is low. The coordinate tensor is rarely the problem. The real problem is what you do with it later.

If your later layers operate on packed tensors in batch, coordinates are cheap metadata. If your later layers repeatedly do irregular neighbor lookups, the gathers and scatters become the bottleneck.

## Good packed representation

A clean baseline layout is:

```text
features:   [N, C]      float16/bfloat16
coords:     [N, D]      int32   # e.g. [batch, y, x] or [batch, z, y, x]
batch_ptr:  [B + 1]     int32   # optional row splits if batching variable counts
```

Where:

- `N` is the number of active cells
- `C` is the feature width
- `D` is the coordinate dimension
- `B` is the batch size

This stays GPU-friendly when most of the work is dense over `N`, not random over the original full grid.

## Why explicit coordinates are usually cheap

For a rough memory comparison:

- `features` with `C=128` in `fp16` cost `256 bytes` per active cell
- `coords` with `D=3` in `int32` cost `12 bytes` per active cell
- `coords` with `D=4` in `int32` cost `16 bytes` per active cell

So coordinates are often only a small fraction of the packed feature storage. Even with extra indexing metadata, they are usually much cheaper than carrying a large dense inactive region through the model.

## Where GPU performance actually breaks

The bad case is not "I stored coordinates."

The bad case is:

- every layer rebuilds neighborhoods from scratch
- every token does separate random lookups into a large structure
- execution turns into many small gather/scatter kernels
- access is uncoalesced, so memory bandwidth gets wasted
- control flow becomes highly irregular across threads

That is the pattern that feels like "lots of memory calls."

## GPU-friendly patterns

These tend to work well with packed active cells:

- Pointwise ops on `[N, C]`: MLPs, norms, projections
- Global attention over packed tokens when `N` is moderate
- Windowed attention after sorting by tile or space-filling order
- Segmented reductions by batch, tile, or region
- One-time preprocessing to build reusable adjacency/index structures
- Scatter back to dense only at the boundary where a dense consumer needs it

In other words: dense math over packed rows is good. Repeated pointer chasing is not.

## Patterns that turn into gather/scatter hell

These are the ones to avoid or heavily constrain:

- Python-side loops over active cells
- Recomputing k-nearest or local neighborhoods every layer
- Hash-table style lookups inside the hot path
- Per-token neighbor expansion with variable-length random fetches
- Fine-grained scatter updates back into a dense grid at every block
- Frequent conversions between packed and dense layouts

If you see many tiny kernels and low utilization in profiling, this is usually why.

## A practical layout that scales better

If later layers need local structure, do not rely on raw coordinates alone. Precompute structure once.

Useful options:

- Sort active cells by `(batch, tile_y, tile_x, y, x)`
- Bucket active cells into fixed-size tiles or windows
- Build `tile_ptr` or `window_ptr` arrays for segment boundaries
- Build CSR/COO-style neighbor indices if graph-like local ops are required
- Keep an optional dense lookup map only for preprocessing or occasional boundary ops

That gives you:

- contiguous chunks for nearby cells
- better memory coalescing
- fewer random accesses
- easier blockwise kernels

## Recommended strategy by downstream operation

### If you only need absolute position

Use:

- `features [N, C]`
- `coords [N, D]`

This is the easy case. The coordinate overhead is negligible.

### If you need local neighborhoods

Use:

- packed features
- packed coordinates
- precomputed neighborhood structure

Do not perform ad hoc random neighbor search in every layer.

### If you need attention

Use one of:

- full attention on packed tokens when `N` is small enough
- tiled or windowed attention after spatial sorting
- block-sparse attention if locality matters and `N` is larger

The main scaling issue here is attention complexity, not coordinate storage.

## A reasonable pipeline

1. Start from a dense mask or sparse event source.
2. Extract active cells into `features [N, C_in]`.
3. Store `coords [N, D]`.
4. Sort or bucket by spatial locality.
5. Build reusable segment or adjacency metadata once.
6. Run most of the model in packed space.
7. Convert back to dense only if a later stage strictly requires it.

## Rule of thumb

Packed active cells are usually worth it when:

- occupancy is low
- inactive regions would dominate dense compute
- most layers can run directly on `[N, C]`
- spatial structure can be bucketed or preindexed

Packed active cells are less attractive when:

- occupancy is already high
- every layer needs dense regular stencil access
- the model keeps bouncing between sparse and dense forms
- local connectivity is extremely dynamic and expensive to rebuild

## A simple mental model

Coordinates are cheap.

Random access is expensive.

Dense math over packed rows is what you want. If local structure matters, precompute it once and keep the hot path regular.
