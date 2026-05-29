# Phase 6: PE Integration

## Pasteable Goal

Integrate PE as a thin downstream consumer of the standalone FlashMask package,
preserving PE's dense state-autoregressive semantics while routing FlashMask
attention through interval metadata and the public FlashMask API. The current
strict GPU integration target is SM86/SM8x; SM90/Hopper integration remains a
templated, hard-gated validation path until H100/H200 access is available. See
`/home/jake/Developer/flashmask/goal/phase-6-pe-integration.md` for the
detailed scope, tests, and exit criteria.

## Objective

Keep PE as a thin downstream consumer of the standalone FlashMask package.

This phase connects the completed standalone FlashMask API to PE without moving
experiment policy, tokenization, batching, model logic, training, or evaluation
out of PE.

PE integration proof should be strict for the available SM86/SM8x backend. PE
should expose SM90/Hopper aliases and artifact fields only as a fail-closed
template path until the standalone FlashMask SM90 proof has passed on Hopper.

## Phase 5 Handoff

Phase 5 completion evidence is recorded in
`/home/jake/Developer/flashmask/goal/phase-5-completion-audit.md`.

The standalone package now exposes one public routed API:

```text
public request: backend="auto"
local selected backend: fa2-compatible
backend kind: sm8x_sparse_fa2_compatible
device: SM86, compute capability 8.6
forward: verified
backward: verified
training: verified through PE tiny training and GPU parity tests
```

PE already has the Phase 5 adapter shape:

- `components.attention.FLASHMASK_BACKEND = "flashmask"`
- package backend mapping: `flashmask -> auto`
- explicit proof/diagnostic alias: `flashmask-fa2-compatible -> fa2-compatible`
- explicit proof/diagnostic alias: `flashmask-fa3 -> fa3`
- benchmark/proof artifacts record `requested_backend`, `selected_backend`,
  `backend_kind`, capability, and forward/backward readiness.

Phase 6 should use the plain PE backend name `flashmask` for normal PE
training/eval integration. Explicit `flashmask-fa2-compatible` and
`flashmask-fa3` should remain available for hardware proof, diagnostics, and
negative routing tests, not as the default PE policy.

## Non-Goals

- Do not put PE experiment policy into `flashmask`.
- Do not put cellular automata rollout logic into `flashmask`.
- Do not duplicate PE tokenization or positional encoding logic inside
  `flashmask`.
- Do not silently fall back from FlashMask to dense SDPA after a FlashMask
  backend is requested.
- Do not enable FlashMask training unless the selected backend reports backward
  readiness.

## Dependency Contract

PE should depend on `flashmask` as an editable local `uv` path dependency,
analogous to `ankos`.

Expected dependency shape:

```toml
flashmask = { path = "/home/jake/Developer/flashmask", editable = true }
```

The PE lockfile should record the editable source mapping. Tests should verify
that `import flashmask` resolves to the workspace source tree during local
development.

## Ownership Boundary

PE owns:

- stream manifests and experiment policy
- tokenization
- positional encodings
- batching and tensor assembly
- model architecture
- train/eval loops
- rollout scoring
- dense SDPA reference backend

FlashMask owns:

- `IntervalMask`
- PE metadata to interval-mask compilation
- dense reconstruction for FlashMask tests
- backend router
- PyTorch extension calls
- sparse kernel-native attention

`ankos` owns:

- raw cellular automata mechanics
- NumPy-style rollout outputs

## PE Adapter Points

The integration should stay narrow.

Expected PE-side adapter points:

- attention backend names such as:
  - `sdpa`
  - `flashmask`
  - optional explicit aliases for tests, such as `flashmask-fa3` or
    `flashmask-fa2-compatible`
- batch helpers that compile PE metadata into `flashmask.IntervalMask`
- model attention calls that pass `IntervalMask` to `flashmask_attention`
- training backend validation that calls `flashmask.verify_backend`
- eval/rollout code that builds query masks for incremental cache decode

PE should not call raw `torch.ops.flashmask.*` directly except in low-level
tests. Normal PE code should use the public Python FlashMask API.

After Phase 5, any remaining Phase 6 work should audit and complete PE
end-to-end usage rather than rebuild the router. In particular, check that
training, eval, sampler rollout, and benchmark code prefer `flashmask` for the
public path and only use explicit aliases where an architecture-specific proof
is being requested.

## Full-Sequence Mask Integration

For standard next-state training/eval batches, PE provides:

```text
time_index:    [B, T]
token_type_id: [B, T]
valid_token:   [B, T]
```

PE should call the FlashMask PE compiler to produce an interval mask matching
the dense PE reference:

```text
query metadata == key metadata
```

The resulting mask must preserve:

- BOS visibility
- domain-token visibility
- same-timestep state visibility
- future-state exclusion
- padding exclusion
- mask-head broadcasting

## Cached Rollout Query Integration

For incremental rollout with K/V cache, PE decodes one block at a time.

For each decoded block, PE provides:

```text
query_time_index: [B, Q]
query_token_type: [B, Q]
key_time_index:   [B, K]
key_token_type:   [B, K]
key_valid_token:  [B, K]
```

where key metadata is accumulated cache metadata plus the current decoded block.

PE should compile this into a query `IntervalMask` and pass it to the same model
attention path. Dense rollout remains a reference path only.

## Attention Backend Behavior

Dense reference backend:

- uses PE's existing dense boolean mask and SDPA path
- remains available for tests, debugging, and comparison

FlashMask backend:

- accepts only `flashmask.IntervalMask` or PE metadata that compiles into one
- calls `flashmask.flashmask_attention`
- uses PE `flashmask` -> package `backend="auto"` for the normal public route
- records selected FlashMask backend info when needed
- does not use dense boolean masks as a fallback

If FlashMask is requested and unavailable, PE should fail clearly.

## Training Gate

Before training starts with a FlashMask backend, PE must verify:

```python
flashmask.verify_backend(
    backend=attention.flashmask_package_backend(pe_backend),
    require_backward=True,
)
```

For the normal PE backend `flashmask`, this verifies package `backend="auto"`.
For explicit proof aliases, this verifies the corresponding explicit package
backend request.

Training should fail before the first optimizer step if:

- the package is missing
- the extension is missing
- the selected GPU/backend is unsupported
- forward is unavailable
- backward is unavailable
- backend readiness metadata is inconsistent

Inference/eval may allow forward-only backends if the call path does not require
gradients, but that must be explicit and tested.

## Model Integration

The model attention path should:

- preserve PE's existing dense SDPA backend
- add a FlashMask backend through the same attention abstraction
- reject dense boolean masks when a FlashMask backend is selected
- reject `IntervalMask` objects when a dense backend is selected unless
  explicitly converting for reference tests
- preserve GQA behavior according to FlashMask router support
- keep attention output shape identical across backends

Any K/V head expansion required by FlashMask should be centralized and tested,
not scattered through PE.

## Eval And Rollout Integration

Evaluation should compare dense and FlashMask outputs for:

- logits
- loss
- next-state token scores
- cached rollout scores
- generated rollout behavior where deterministic sampling is used

Rollout code should build query masks from cache metadata, not from flattened
token-order causality.

## Test Requirements

PE tests should cover:

- editable `uv` path dependency
- FlashMask import resolves to workspace source
- full-sequence dense mask equals FlashMask dense reconstruction
- query/incremental dense mask equals FlashMask dense reconstruction
- FlashMask attention path receives `IntervalMask`, not dense tensors
- FlashMask backend does not fall back to SDPA
- unsupported FlashMask backend fails clearly
- training backend rejects missing backward
- training backend accepts ready backward
- logits parity against dense reference
- loss parity against dense reference
- cached rollout parity against dense reference

GPU tests should be hard-gated and architecture-specific. They should fail loud
when explicitly required.

SM86/SM8x GPU tests are the current Phase 6 runtime proof target. SM90/Hopper
GPU tests should be documented and hard-gated, but they are deferred and are not
required for current Phase 6 completion without H100/H200 hardware.

## Benchmark Readiness

This phase should not prove final speedup. It should only make PE capable of
running the correct FlashMask path.

Benchmark proof belongs to Phase 7. Phase 6 should ensure benchmark inputs are
representative and that artifacts can record:

- requested FlashMask backend
- selected FlashMask backend
- backend kind
- GPU capability
- mask density
- full-sequence versus rollout-shaped case

## Failure Modes To Test

- `flashmask` package missing
- extension missing
- unsupported GPU
- explicit backend mismatch
- dense mask passed to FlashMask backend
- `IntervalMask` shape does not match Q/K length
- training requested with forward-only backend
- unsupported GQA
- padding or time metadata mismatch

All errors should explain the PE-side action or FlashMask build requirement.

## Test Commands

Focused PE tests:

```bash
PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu pytest -q \
  tests/test_attention.py \
  tests/test_batch.py \
  tests/test_train.py \
  tests/test_flashmask_verification_gates.py
```

GPU PE parity tests, hard-gated by backend:

```bash
PYTHONPATH=/home/jake/Developer/flashmask/src PE_REQUIRE_FLASHMASK_SM8X=1 uv run --extra gpu pytest -q \
  tests/test_train.py \
  tests/test_flashmask_sm8x_gpu_parity.py
```

Broader PE regression suite:

```bash
PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu pytest -q
```

Deferred Hopper PE parity command:

```bash
PE_REQUIRE_FLASHMASK_SM90=1 PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu pytest -q tests/test_flashmask_gpu_parity.py
```

The exact test filenames may change, but the commands must prove PE calls the
standalone FlashMask package and not framework reference code.

## Exit Criteria

- PE depends on FlashMask through editable `uv` path dependency.
- PE imports the workspace FlashMask source during local tests.
- PE full-sequence masks match dense SDPA semantics.
- PE cached rollout query masks match dense SDPA semantics.
- PE logits and loss match dense SDPA on representative next-state batches.
- PE cached rollout scores match dense behavior.
- PE tests verify the FlashMask path receives interval metadata, not dense masks.
- PE tests verify FlashMask backends do not fall back to dense SDPA.
- Training uses FlashMask only when the selected backend has backward support.
- SM86/SM8x PE GPU parity is the current strict GPU integration target.
- SM90/Hopper PE GPU parity remains documented and fail-closed until deferred
  Hopper validation is run.
- PE remains a thin consumer; FlashMask remains the owner of sparse masks and
  kernel-native attention.
