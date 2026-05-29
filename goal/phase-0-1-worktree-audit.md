# Phase 0-1 Worktree Audit

Goal: reset the current work around the standalone-kernel architecture, keep only
the pieces that prove the mask specification in Phase 0-1, and defer kernel,
router, integration, and benchmark proof work to later phases.

Details: `/home/jake/Developer/flashmask/goal/phase-0-1-reset-and-mask-spec.md`

## Snapshot

Observed from `/home/jake/Developer/flashmask` and `/home/jake/Developer/pe`.

- Initial FlashMask worktree status before adding this audit: clean.
- PE worktree status: dirty with FlashMask-related integration, tests, benchmark
  scaffolding, and editable dependency changes.
- Runtime dependency audit: no `paddle` or `paddlenlp` imports were found in
  FlashMask `src`, `tests`, `pyproject.toml`, or `README.md`.
- No files were reverted or destructively reset during this audit.

## FlashMask Classification

Keep for Phase 0-1:

- `src/flashmask/core.py`
  - Owns `IntervalMask`, canonical `[B, mask_heads, K, bound_num]`
    `startend_row_indices` validation, dense boolean reconstruction, additive
    reconstruction, and dense-to-interval compilation.
  - This is the core standalone mask specification.
- `src/flashmask/pe.py`
  - Owns PE-compatible state-causal and query/key state-causal interval
    compilers without importing PE, Torch, or GPU libraries.
  - Preserves the important next-state behavior: state queries at timestep `t`
    may see BOS/domain keys and state keys with `key_time <= t`.
- `src/flashmask/builders.py`
  - Owns lightweight structured-mask builders for causal, sliding-window,
    document, prefix-LM, and dense-imported masks.
  - This keeps the package flexible for the other mask families in
    `context/masks.py`.
- `tests/test_core.py`
  - Proves interval reconstruction, causal bottom-right alignment, additive
    masks, and dense compiler representability.
- `tests/test_pe.py`
  - Proves PE state-causal full and incremental query compilers match dense
    references, preserve same-timestep state visibility, repeat mask heads, and
    reject non-contiguous intervals.
- `tests/test_builders.py`
  - Proves the structured builders reconstruct the masks shown in
    `context/masks.py`.

Rework in later phases:

- `src/flashmask/attention.py`
  - Useful public API shape and fail-closed behavior, but real backend readiness
    belongs to Phases 2, 5, and 8.
- `src/flashmask/_backend.py`
  - Useful optional extension loader and validation path, but the ABI and router
    contract should be finalized in Phases 2 and 5.
- `src/flashmask/csrc/*`
  - Kernel source belongs to Phases 2-4. It should not be used as Phase 0-1
    completion evidence except to confirm that the package boundary stays
    standalone.
- `tests/test_backend_contract.py`, `tests/test_cuda_extension_optional.py`,
  `tests/test_cuda_pe_parity_optional.py`, and `tests/test_package_surface.py`
  - Useful for later-phase backend contract, package surface, and GPU parity
    gates. They are not required to prove Phase 0-1 mask semantics.

Defer:

- `src/flashmask/bench_sm90.py` and `src/flashmask/proof.py`
  - Benchmark and profiler proof belongs to Phase 7 after forward, backward,
    router, and PE integration are real.

## PE Classification

Keep for Phase 0-1:

- `components/batch.py`
  - `build_state_causal_query_mask` is the dense reference policy.
  - `compile_state_causal_flashmask_mask` and
    `compile_state_causal_flashmask_query_mask` are the right thin PE adapters:
    PE supplies tokenizer/time metadata; FlashMask compiles interval metadata.
  - `flashmask_pe_token_types` correctly maps PE tokenizer constants into the
    standalone FlashMask `PETokenTypeIds` boundary.
- `tests/test_batch.py`
  - The FlashMask additions directly prove PE dense mask parity for full
    sequence masks, incremental query masks, same-timestep state visibility,
    mask-head repetition, non-contiguous rejection, and batch attention-mask
    assembly.

Rework in later phases:

- `components/attention.py`
  - The backend names, fail-closed routing, and GQA expansion are useful, but
    should be finalized against the Phase 5 FlashMask router rather than treated
    as Phase 0-1 completion.
- `model.py`, `components/sampler.py`, `train.py`,
  `components/evals.py`, `tests/test_attention.py`, and `tests/test_train.py`
  - These belong to PE integration and training readiness in Phases 6 and 7.
    They should remain fail-closed until FlashMask reports verified forward and
    backward support for the selected backend.
- `pyproject.toml` and `uv.lock`
  - Editable FlashMask path dependency is useful, but dependency wiring is a
    Phase 6 integration concern.

Defer or quarantine:

- `benchmarks/bench_flashmask_attention.py`
  - Benchmark proof belongs to Phase 7.
- `tests/test_flashmask_gpu_parity.py`
  - SM90 GPU parity/profiler proof belongs to Phases 6-7.
- `tests/test_flashmask_sm8x_gpu_parity.py`
  - SM80/SM86 GPU parity/profiler proof belongs to Phases 6-7.
- `tests/test_flashmask_verification_gates.py`
  - Verification-gate and benchmark artifact tests belong to Phase 7.
- `README.md` benchmark instructions
  - User-facing proof instructions should be finalized only after Phase 7
    evidence exists.

## Phase 0-1 Decision

The Phase 0-1 implementation target is the mask specification, not the kernel.
The current mask-spec files are in the correct architectural locations:
FlashMask owns interval metadata and dense references; PE owns policy,
tokenization, batching, tensors, model, train, and eval.

The next work should run only the mask/spec-focused tests as completion evidence.
GPU parity, profiler markers, benchmark speedups, training checks, and final
router behavior must stay out of the Phase 0-1 completion claim.
