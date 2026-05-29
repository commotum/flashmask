# Phase 7 Completion Audit

Date: 2026-05-29

Goal reference:
`/home/jake/Developer/flashmask/goal/phase-7-benchmarks-and-proof.md`.

## Status

Phase 7 is complete for the current local SM86/SM8x proof target.

The validated proof set is:

- `/home/jake/Developer/pe/artifacts/pe-flashmask-sm86-full.jsonl`
- `/home/jake/Developer/pe/artifacts/pe-flashmask-sm86-rollout.jsonl`
- `/home/jake/Developer/pe/artifacts/pe-flashmask-sm86.jsonl`

SM90/Hopper proof remains a deferred template target until H100/H200 hardware
is available. SM80/A100 proof remains a separate deferred target. Training
speed is not claimed by this phase.

## Requirements And Evidence

- SM86 sparse backend readiness:
  - Evidence command from `/home/jake/Developer/pe`:
    `PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu pytest -q tests/test_flashmask_sm8x_gpu_parity.py tests/test_flashmask_verification_gates.py`
  - Result: `22 passed in 17.92s`.
  - Coverage: backend selection, fail-closed gates, attention parity, model
    logits/loss parity, cached rollout parity, profiler marker checks, and
    proof command/documentation assertions.

- Full-sequence PE attention proof:
  - Artifact:
    `/home/jake/Developer/pe/artifacts/pe-flashmask-sm86-full.jsonl`
  - Benchmark command from `/home/jake/Developer/pe`:
    `PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu python benchmarks/bench_flashmask_attention.py --backend fa2-compatible --require-sm86 --cases full --dtypes bf16 --batch-sizes 1 --seq-lens 4096 --heads 4 --head-dim 128 --warmup 20 --iters 100 --min-speedup 1.5 --jsonl --output-jsonl artifacts/pe-flashmask-sm86-full.jsonl`
  - Result:
    `dense_sdpa_ms=1.118592`, `flashmask_ms=0.443840`,
    `speedup=2.520260`, `correctness_passed=True`,
    `profiler_passed=True`, `speed_proof_passed=True`.

- Rollout-shaped PE eval attention proof:
  - Artifact:
    `/home/jake/Developer/pe/artifacts/pe-flashmask-sm86-rollout.jsonl`
  - Benchmark command from `/home/jake/Developer/pe`:
    `PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu python benchmarks/bench_flashmask_attention.py --backend fa2-compatible --require-sm86 --cases rollout --dtypes bf16 --batch-sizes 32 --seq-lens 4096 --heads 4 --head-dim 128 --warmup 20 --iters 100 --min-speedup 1.5 --jsonl --output-jsonl artifacts/pe-flashmask-sm86-rollout.jsonl`
  - Result:
    `B=32`, `Q=128`, `K=3970`, `query_density=1.0`,
    `dense_sdpa_ms=1.271552`, `flashmask_ms=0.665840`,
    `speedup=1.909696`, `correctness_passed=True`,
    `profiler_passed=True`, `speed_proof_passed=True`.
  - Representativeness:
    PE defaults use `DEFAULT_EVAL_BATCH_SIZE=64` and
    `DEFAULT_EVAL_BATCH_TOKENS=131072`; for 4096-token episodes, the token cap
    gives a 32-row eval batch. The rollout record uses the actual incremental
    cache length through the decoded block, `K=3970`, not padded capacity.

- Combined proof validation:
  - Assembly command from `/home/jake/Developer/pe`:
    `cat artifacts/pe-flashmask-sm86-full.jsonl artifacts/pe-flashmask-sm86-rollout.jsonl > artifacts/pe-flashmask-sm86.jsonl`
  - Validator command from `/home/jake/Developer/pe`:
    `PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu flashmask-validate-proof --backend fa2-compatible --min-speedup 1.5 --require-case full --require-case rollout artifacts/pe-flashmask-sm86.jsonl`
  - Result: `validated 2 FlashMask SM86 proof records`.

- Proof validator hardening:
  - Evidence command from `/home/jake/Developer/flashmask`:
    `uv run pytest -q tests/test_proof.py tests/test_package_surface.py`
  - Result: `63 passed in 2.07s`.
  - Coverage: skipped profiler checks, missing kernel markers, dense fallback
    events, failed correctness/speed flags, missing required cases, invalid
    timing fields, invalid `backward_ready`, and too-low `min_speedup` are
    rejected.

- Regression coverage:
  - FlashMask command from `/home/jake/Developer/flashmask`:
    `uv run pytest -q`
  - Result: `125 passed, 26 skipped in 2.19s`.
  - PE command from `/home/jake/Developer/pe`:
    `PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu pytest -q`
  - Result: `120 passed, 10 skipped in 18.84s`.

## Deferred Targets

- SM90/Hopper runtime proof:
  - Deferred until H100/H200 hardware is available.
  - Phase 7 keeps SM90 artifact paths, commands, and validator target
    documented.

- SM80/A100 runtime proof:
  - Deferred until SM80/A100-class hardware is available.
  - Local SM86/A6000 artifacts are not used to claim SM80 speed.

- Training speed proof:
  - Not claimed.
  - A separate train-step benchmark remains required before any training
    speedup claim.

## Conclusion

The current SM86/SM8x FlashMask sparse backend has reproducible PE full and
rollout-shaped benchmark artifacts that are correct, profiler-verified, and
above the configured `1.5x` speedup gate against PE dense masked SDPA on the
representative local proof shapes.
