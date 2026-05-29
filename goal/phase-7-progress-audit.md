# Phase 7 Progress Audit

Date: 2026-05-29

Goal reference:
`/home/jake/Developer/flashmask/goal/phase-7-benchmarks-and-proof.md`.

## Current Status

The SM86 PE attention proof set now validates for both required attention
workloads:

- full-sequence PE attention at `B=1`
- rollout-shaped cached PE eval attention at `B=32`, matching the default
  `DEFAULT_EVAL_BATCH_TOKENS=131072` cap for 4096-token episodes

Training speed is not claimed; a separate train-step benchmark remains
required before making any train-speed claim.

## Tooling Completed

- `src/flashmask/csrc/flashmask_v2/mainloop_fwd_sm80.hpp`
  - SM8x forward now uses existing FlashMask min/max metadata to detect
    fully unmasked non-causal interval tiles and skip the per-score direct
    interval predicate on those tiles.
  - This preserves masked and edge tiles on the existing path while reducing
    overhead on dense-prefix rollout and full-sequence tiles.

- `/home/jake/Developer/pe/benchmarks/bench_flashmask_attention.py`
  - Records PE and FlashMask source paths.
  - Records PE and FlashMask git commits and dirty flags.
  - Records FlashMask build mode and relevant build-selection environment
    variables.
  - Records seed, warmup count, and iteration count in each benchmark record.
  - Rollout now benchmarks the actual incremental cache K length through the
    decoded state block instead of the padded max sequence capacity.

- `src/flashmask/proof.py`
  - Now rejects proof records unless `correctness_passed`,
    `profiler_passed`, `speedup_passed`, and `speed_proof_passed` are all
    true.
  - Requires `backward_ready` to be present and boolean.
  - Requires finite positive `dense_sdpa_ms` and `flashmask_ms`.
  - Requires `min_speedup` in the record to meet the validator threshold.

## Passing Evidence

- Focused FlashMask proof/package tests:
  - Command from `/home/jake/Developer/flashmask`:
    `uv run pytest -q tests/test_proof.py tests/test_package_surface.py`
  - Result after current proof-validator and CUDA changes:
    `63 passed in 2.07s`.

- PE benchmark/gate tests:
  - Command from `/home/jake/Developer/pe`:
    `PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu pytest -q tests/test_flashmask_verification_gates.py`
  - Result after rollout benchmark correction: `14 passed in 15.83s`.

- PE SM8x parity and verification gates after the SM8x CUDA tile-skip change:
  - Command from `/home/jake/Developer/pe`:
    `PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu pytest -q tests/test_flashmask_sm8x_gpu_parity.py tests/test_flashmask_verification_gates.py`
  - Result after rebuilding the retained SM8x tile-skip extension:
    `22 passed in 17.92s`.

- FlashMask full regression suite:
  - Command from `/home/jake/Developer/flashmask`:
    `uv run pytest -q`
  - Result after current changes: `125 passed, 26 skipped in 2.19s`.

- PE full regression suite:
  - Command from `/home/jake/Developer/pe`:
    `PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu pytest -q`
  - Result after current changes: `120 passed, 10 skipped in 18.84s`.

- SM86 full-sequence proof artifact:
  - Artifact:
    `/home/jake/Developer/pe/artifacts/pe-flashmask-sm86-full.jsonl`
  - Benchmark command from `/home/jake/Developer/pe`:
    `PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu python benchmarks/bench_flashmask_attention.py --backend fa2-compatible --require-sm86 --cases full --dtypes bf16 --batch-sizes 1 --seq-lens 4096 --heads 4 --head-dim 128 --warmup 20 --iters 100 --min-speedup 1.5 --jsonl --output-jsonl artifacts/pe-flashmask-sm86-full.jsonl`
  - Result:
    `dense_sdpa_ms=1.118592`, `flashmask_ms=0.443840`,
    `speedup=2.520260`, `correctness_passed=True`,
    `profiler_passed=True`, `speed_proof_passed=True`.
  - Validator command:
    `PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu flashmask-validate-proof --backend fa2-compatible --min-speedup 1.5 --require-case full artifacts/pe-flashmask-sm86-full.jsonl`
  - Result: `validated 1 FlashMask SM86 proof records`.

## Rollout Proof Evidence

- SM86 rollout proof artifact:
  - Artifact:
    `/home/jake/Developer/pe/artifacts/pe-flashmask-sm86-rollout.jsonl`
  - Benchmark command from `/home/jake/Developer/pe`:
    `PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu python benchmarks/bench_flashmask_attention.py --backend fa2-compatible --require-sm86 --cases rollout --dtypes bf16 --batch-sizes 32 --seq-lens 4096 --heads 4 --head-dim 128 --warmup 20 --iters 100 --min-speedup 1.5 --jsonl --output-jsonl artifacts/pe-flashmask-sm86-rollout.jsonl`
  - Result:
    `B=32`, `Q=128`, `K=3970`, `query_density=1.0`,
    `dense_sdpa_ms=1.271552`, `flashmask_ms=0.665840`,
    `speedup=1.909696`, `correctness_passed=True`,
    `profiler_passed=True`, `speedup_passed=True`,
    `speed_proof_passed=True`, `status=ok`.

- Combined full + rollout proof artifact:
  - Artifact:
    `/home/jake/Developer/pe/artifacts/pe-flashmask-sm86.jsonl`
  - Assembly command from `/home/jake/Developer/pe`:
    `cat artifacts/pe-flashmask-sm86-full.jsonl artifacts/pe-flashmask-sm86-rollout.jsonl > artifacts/pe-flashmask-sm86.jsonl`
  - Validator command:
    `PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu flashmask-validate-proof --backend fa2-compatible --min-speedup 1.5 --require-case full --require-case rollout artifacts/pe-flashmask-sm86.jsonl`
  - Result:
    `validated 2 FlashMask SM86 proof records`.

## Rollout Diagnostics

Before the SM8x fully-unmasked-tile skip, rollout-only samples on padded
`K=4096`, `B=1`, `H=4`, `D=128`, BF16 showed:

```text
rollout_state_tokens=128:  speedup=0.568
rollout_state_tokens=256:  speedup=0.582
rollout_state_tokens=512:  speedup=0.591
rollout_state_tokens=1024: speedup=0.672
rollout_state_tokens=2048: speedup=1.417
```

The representative PE state-block sizes in the current manifests are much
closer to 1, 121, 123, 125, and related OOD-scale sizes than to 2048. The
2048-token sample is therefore not sufficient to satisfy the rollout-shaped PE
proof requirement, and it still did not meet the documented 1.5 threshold in
the diagnostic run.

The original rollout benchmark was also slightly pessimistic because it used
the padded max sequence capacity as K. The real PE `forward_incremental` path
passes the accumulated cache length through the decoded block. For the
representative `seq_len=4096`, `rollout_state_tokens=128` case, that is
`K=3970`, not `K=4096`.

After correcting the benchmark and rebuilding the SM8x extension with the
fully-unmasked-tile skip, the single-row rollout diagnostic is:

```text
B=1, Q=128, K=3970, query_density=1.0
dense_sdpa_ms=0.365200
flashmask_ms=0.385440
speedup=0.947489
```

That result is a large improvement over the earlier padded result, but it is a
single-row diagnostic rather than the default PE eval shape. PE defaults use
`DEFAULT_EVAL_BATCH_SIZE=64` and `DEFAULT_EVAL_BATCH_TOKENS=131072`; for
4096-token episodes the token cap gives a 32-row eval batch. The corresponding
rollout-shaped proof record validates:

```text
B=32, Q=128, K=3970, query_density=1.0
dense_sdpa_ms=1.271552
flashmask_ms=0.665840
speedup=1.909696
```

An all-open interval sentinel experiment was also tested and removed. It
skipped FlashMask preprocessing and profiled only the CUTLASS forward kernel,
but the representative rollout result regressed to approximately
`dense_sdpa_ms=0.351872`, `flashmask_ms=0.585008`, `speedup=0.601482`. That
path is therefore not retained and is not part of the current proof surface.

Timing the actual mask builders inside the loop is not a valid workaround:
FlashMask interval compilation is currently Python-heavy and measured around
`220 ms` for the sampled rollout case, so including it in the timed region
would measure compiler overhead rather than the sparse kernel proof target.

## Remaining Work

- Keep SM90/Hopper runtime proof deferred until H100/H200 hardware is
  available.
- Add a separate train-step benchmark before claiming training speedup.

## Conclusion

Phase 7 has made concrete progress: proof tooling is stricter, reproducibility
metadata is recorded, and the SM86 full + rollout attention proof set now
validates. Training speed remains unclaimed.
