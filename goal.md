# Goal

Build `flashmask` as a small standalone package that PE can depend on for fast FlashAttention-compatible sparse attention masks on both SM90 and SM86-class NVIDIA GPUs.

## Boundary

- `flashmask` owns sparse mask representation, mask compilation, dense-reference reconstruction for tests, and the kernel-native attention interface.
- `flashmask` must be standalone and must not depend on large external training frameworks.
- `pe` owns experiment policy, tokenization, positional encodings, batching, torch tensors, model code, training, and evaluation.
- `ankos` owns raw cellular automata mechanics and NumPy-style rollout outputs.

## Execution Priority

The kernel is the critical path. Build and verify kernel-native sparse forward paths before expanding PE integration beyond a minimal fail-closed adapter. SM90 should use the FlashAttention 3-compatible path. SM86 must use an exact sparse interval path, likely FA2-compatible or custom, rather than stock FA2 masking. PE should be used as the downstream correctness and benchmark harness after the kernel path is real.

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
- SM90 validation verifies the FlashAttention 3-compatible sparse path.
- SM86 validation verifies an exact sparse interval kernel path that preserves PE semantics; stock FA2 causal/window/padding masks are not sufficient.
- GPU integration tests run from the PE repo and verify FlashMask produces scores/logits/losses identical to the dense implementation within tolerance.
- GPU integration tests verify the FlashMask path actually uses the intended kernel backend for the active GPU architecture.
- Benchmarks from the PE repo show the FlashMask path is materially faster than the dense SDPA mask path on representative next-state workloads.
- The package can be installed, imported, tested, and used without importing large external training frameworks.
