# Goal

Build `flashmask` as a small standalone package that PE can depend on for fast FlashAttention 3-compatible sparse attention masks.

## Boundary

- `flashmask` owns sparse mask representation, mask compilation, dense-reference reconstruction for tests, and the kernel-native attention interface.
- `flashmask` must not depend on Paddle or PaddleNLP; those projects are reference material only.
- `pe` owns experiment policy, tokenization, positional encodings, batching, torch tensors, model code, training, and evaluation.
- `ankos` owns raw cellular automata mechanics and NumPy-style rollout outputs.

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
- The fast path calls a FA3-compatible kernel-native sparse attention implementation rather than dense SDPA with an attention mask.
- GPU integration tests run from the PE repo and verify FlashMask produces scores/logits/losses identical to the dense implementation within tolerance.
- GPU integration tests verify the FlashMask path actually uses FlashAttention 3.
- Benchmarks from the PE repo show the FlashMask path is materially faster than the dense SDPA mask path on representative next-state workloads.
- The package can be installed, imported, tested, and used without importing Paddle or PaddleNLP.
