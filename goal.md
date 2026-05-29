# Goal

Build `flashmask` as a small standalone PyTorch package that PE can depend on
for fast FlashAttention-compatible sparse attention masks across supported
NVIDIA GPUs.

## Boundary

- `flashmask` owns sparse mask representation, mask compilation, dense-reference reconstruction for tests, and the kernel-native attention interface.
- `flashmask` must be standalone and must not depend on large external training
  frameworks; external framework implementations are reference material only.
- `pe` owns experiment policy, tokenization, positional encodings, batching, torch tensors, model code, training, and evaluation.
- `ankos` owns raw cellular automata mechanics and NumPy-style rollout outputs.

## Execution Priority

The kernel port is the critical path. First extract/adapt the FlashMask paper's
known interval-mask kernel design into a standalone PyTorch CUDA extension with
no runtime dependency on external training frameworks. Build and verify
kernel-native sparse forward and backward paths before expanding PE integration
beyond a minimal fail-closed adapter.

PE should be used as the downstream correctness, training, and benchmark
harness only after the standalone kernel path is real.

## Current Hardware Strategy

The current local development GPU is an NVIDIA RTX A6000 with compute
capability 8.6. Current completion criteria should therefore be strict for the
SM86/SM8x backend and should not require access to SM90/Hopper hardware.

SM90/Hopper work should still be laid out deliberately: keep source templates,
build modes, backend metadata, router branches, fail-closed validation, and
hard-gated H100/H200 proof commands in place. Runtime parity, profiler evidence,
and benchmark proof for SM90 are deferred until Hopper hardware is available.
Do not claim SM90 support until those deferred proof commands pass on Hopper.

## Backend Router

`flashmask` must expose one stable Python attention API and route internally to
the best available sparse backend:

- SM80/SM86-class GPUs: current strict implementation and proof target. Use an
  exact FA2-compatible or custom sparse interval path that preserves the same
  mask semantics.
- SM90 / compute capability 9.0: maintain a FlashAttention 3-compatible
  template path with build, metadata, router, and fail-closed hooks. Runtime
  validation is deferred until H100/H200 access is available.
- Unsupported or unbuilt backends must fail closed with an actionable error;
  they must not silently fall back to dense SDPA masking.
- Routing decisions must be observable in tests and benchmark artifacts.

## Primary Behavior

`flashmask` must implement PE's state-autoregressive next-state attention exactly:

- Predict state `t + 1` from a timestep-causal context over states `<= t`.
- Preserve PE's current dense-mask semantics, including special tokens, domain tokens, same-timestep state visibility, padding exclusion, and next-state loss targeting.
- Encode disallowed future-state interactions in the attention kernel so skipped interactions are not scored densely and masked afterward.

## Flexibility

The mask representation must also support the structured interval-style masks represented in `context/masks.py`, including causal, sliding-window, document, prefix-LM, blockwise, global-token, QK-sparse, and related variants when they can be expressed as per-key query-row intervals.

## Verifiable Completion Requirements

- `pe` depends on `flashmask` as an editable local path dependency, analogous to its `ankos` dependency.
- `flashmask` exposes a documented Python API for compiling PE token/state metadata into sparse mask metadata.
- A dense reference path reconstructs the equivalent dense attention mask from the sparse representation for correctness tests.
- PE's FlashMask path matches the existing dense SDPA path on logits/loss within agreed numerical tolerance for representative next-state batches.
- Tests cover the primary PE next-state mask and the structured mask families in `context/masks.py`.
- The fast path calls a kernel-native sparse attention implementation rather than dense SDPA with an attention mask.
- Forward and backward are implemented for the sparse kernel path used by PE training.
- SM80/SM86 validation verifies an exact sparse interval kernel path that preserves PE semantics; stock FA2 causal/window/padding masks are not sufficient.
- SM90/Hopper artifacts provide a buildable, fail-closed FA3-compatible
  template path and hard-gated proof commands. SM90 runtime validation is a
  deferred hardware pass, not a blocker for current completion on SM86.
- The backend router selects the correct sparse implementation for the active GPU/build and fails closed when no correct sparse backend is available.
- GPU integration tests run from the PE repo and verify FlashMask produces scores/logits/losses identical to the dense implementation within tolerance.
- GPU integration tests verify the FlashMask path actually uses the intended kernel backend for the active GPU architecture.
- Benchmarks from the PE repo show the FlashMask path is materially faster than the dense SDPA mask path on representative next-state workloads.
- The package can be installed, imported, tested, and used without importing large external training frameworks.
