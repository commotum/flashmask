# Phase 7: Benchmarks And Proof

## Pasteable Goal

Produce reproducible PE benchmark and proof artifacts showing the standalone
FlashMask sparse kernels are correct, profiler-verified, and materially faster
than PE's dense SDPA mask path on representative full and rollout-shaped
workloads. See
`/home/jake/Developer/flashmask/goal/phase-7-benchmarks-and-proof.md` for the
detailed scope, tests, and exit criteria.

## Objective

Prove the standalone sparse kernel is materially faster than dense SDPA masking
on representative PE workloads.

This phase is proof, not development scaffolding. It should run only after the
standalone forward/backward kernels, backend router, and PE integration are real
enough that benchmark results mean something.

## Non-Goals

- Do not use synthetic-only benchmarks as the final PE proof.
- Do not count runs that skip profiler checks as proof.
- Do not count forward-only inference benchmarks as training proof.
- Do not compare against an artificially slow dense reference.
- Do not accept a FlashMask path that falls back to dense SDPA.

## Benchmark Scope

Required benchmark families:

- Full next-state batch attention:
  - PE dense full-sequence mask versus FlashMask interval mask.
  - Measures standard train/eval forward workload.
- Rollout-shaped incremental attention:
  - one decoded state block as Q
  - accumulated special/state cache metadata as K/V
  - measures cached rollout workload
- Model-level PE parity:
  - logits and loss from the actual PE model
  - confirms attention-level speed did not break model semantics
- Optional train-step benchmark:
  - forward + backward + optimizer step
  - required before claiming training speedup

Synthetic query/block benchmarks may remain useful diagnostics, but they are not
final completion evidence unless they are tied to actual PE rollout shapes.

## Dense Baseline

The dense baseline must be PE's existing dense SDPA mask path:

- same Q/K/V shapes
- same dtype
- same PE metadata
- same model configuration
- same device
- same warmup/iteration protocol
- same autocast behavior where applicable

The dense baseline may use dense boolean masks because it is the reference path.
The FlashMask path must not.

## Correctness Requirements

Every benchmark record used as proof must include correctness evidence.

Attention-level records:

- output parity
- LSE parity where available
- max absolute error
- max relative error or a documented reason if relative error is not meaningful
- tolerance used

Model-level records:

- logits parity
- loss parity
- next-state score parity where applicable
- cached rollout score parity where applicable

Training proof records:

- loss parity
- gradient parity or gradient norm parity
- finite gradients
- backward backend readiness

Correctness must pass before speedup is considered.

## Profiler Requirements

Profiler checks are mandatory for proof records.

Required evidence:

- `torch.ops.flashmask.fwd` appears for forward records.
- `torch.ops.flashmask.bwd` appears for backward/train records.
- backend-specific sparse CUDA kernel markers appear.
- preprocessing kernel marker appears when preprocessing runs as a separate
  CUDA kernel.
- dense SDPA, dense matmul, or dense softmax fallback markers do not appear
  inside the FlashMask timed/profiler region.
- selected backend kind matches the active GPU architecture.

Profiler marker names should be stable and documented per backend.

## JSONL Artifact Schema

Benchmark output should be JSONL so proof validators can inspect every record.

Minimum fields:

- `status`
- `passed`
- `case`
- `workload`
- `requested_backend`
- `selected_backend`
- `backend_kind`
- `device`
- `capability`
- `torch_version`
- `cuda_version`
- `B`
- `Q`
- `K`
- `H`
- `D`
- `dtype`
- `mask_density`
- `query_mask_shape`
- `dense_sdpa_ms`
- `flashmask_ms`
- `speedup`
- `min_speedup`
- `correctness_passed`
- `profiler_passed`
- `speedup_passed`
- `speed_proof_passed`
- `max_abs_error`
- `max_rel_error`
- `atol`
- `rtol`
- `profiler_flashmask_fwd`
- `profiler_flashmask_bwd`
- `profiler_flashmask_cuda_kernel_events`
- `profiler_missing_flashmask_cuda_kernel_markers`
- `profiler_dense_attention_events`
- `forward_ready`
- `backward_ready`

Rollout records should also include:

- `rollout_state_tokens`
- `rollout_prefix_state_count`
- `rollout_source_time`
- `rollout_valid_key_tokens`

Model records should also include:

- model config summary
- vocabulary size
- sequence/state shape summary
- logits/loss parity fields

## Proof Validator Rules

Validators should reject records when:

- `status != "ok"`
- `passed is not True`
- `correctness_passed is not True`
- `profiler_passed is not True`
- `speedup_passed is not True`
- `speed_proof_passed is not True`
- profiler checks were skipped
- required FlashMask CUDA markers are missing
- dense fallback events are present
- backend kind does not match requested proof target
- GPU capability does not match requested proof target
- required cases are missing
- tolerance fields are missing
- error metrics exceed tolerance
- speedup is below the configured threshold
- backward readiness is missing for training proof

Validators should support backend-specific proof targets:

- SM90 FA3-compatible proof
- SM80/SM86 exact sparse interval proof
- optional combined proof that requires both artifact sets

## Speedup Gates

Use explicit minimum speedups, not vague "faster" language.

Initial thresholds may be adjusted after real kernels are measured, but each
proof command must specify a threshold such as:

```text
--min-speedup 1.15
```

Separate thresholds may be needed for:

- attention forward
- model forward
- rollout query
- train step

If a workload is correctness-only, it must not be labeled as speed proof.

## Representative PE Workloads

Benchmark cases should reflect real PE use:

- batch sizes used in train/eval
- sequence lengths from configured streams
- state-block widths from CA layouts
- PE special/domain/state token structure
- padding where it appears in real batches
- cached rollout with accumulated K/V metadata
- BF16 as the primary GPU dtype where PE uses BF16

Avoid proving speed on shapes PE does not actually use.

## Reproducibility

Benchmark commands should record:

- git commit or dirty status if available
- FlashMask source path
- PE source path
- build mode
- GPU name and capability
- random seed
- warmup count
- iteration count
- environment flags that affect kernel selection

Commands should be runnable with `uv` from the PE repo.

## Output Locations

Recommended artifact paths:

```text
/home/jake/Developer/pe/artifacts/pe-flashmask-sm90.jsonl
/home/jake/Developer/pe/artifacts/pe-flashmask-sm86.jsonl
/home/jake/Developer/pe/artifacts/pe-flashmask-sm90-train.jsonl
/home/jake/Developer/pe/artifacts/pe-flashmask-sm86-train.jsonl
```

The exact filenames can change, but they should encode backend and workload.

## Test Commands

Example SM90 proof:

```bash
PE_REQUIRE_FLASHMASK_SM90=1 PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu pytest -q tests/test_flashmask_gpu_parity.py
uv run --extra gpu python benchmarks/bench_flashmask_attention.py --backend fa3 --require-sm90 --cases full,rollout --dtypes bf16 --batch-sizes 1,4 --seq-lens 512,2048 --heads 4 --head-dim 128 --warmup 20 --iters 100 --min-speedup 1.15 --jsonl --output-jsonl artifacts/pe-flashmask-sm90.jsonl
uv run --extra gpu flashmask-validate-proof --backend fa3 --min-speedup 1.15 --require-case full --require-case rollout artifacts/pe-flashmask-sm90.jsonl
```

Example SM86 proof:

```bash
PE_REQUIRE_FLASHMASK_SM8X=1 PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu pytest -q tests/test_flashmask_sm8x_gpu_parity.py
uv run --extra gpu python benchmarks/bench_flashmask_attention.py --backend fa2-compatible --require-sm86 --cases full,rollout --dtypes bf16 --batch-sizes 1 --seq-lens 4096 --heads 4 --head-dim 128 --warmup 20 --iters 100 --min-speedup 1.5 --jsonl --output-jsonl artifacts/pe-flashmask-sm86.jsonl
uv run --extra gpu flashmask-validate-proof --backend fa2-compatible --min-speedup 1.5 --require-case full --require-case rollout artifacts/pe-flashmask-sm86.jsonl
```

Example train proof after backward:

```bash
uv run --extra gpu python benchmarks/bench_flashmask_train_step.py --backend auto --min-speedup 1.05 --jsonl --output-jsonl artifacts/pe-flashmask-train.jsonl
```

The exact benchmark scripts may change, but the final commands must remain
documented and reproducible.

## Exit Criteria

- SM90 proof validates the FA3-compatible sparse backend.
- SM80/SM86 proof validates the exact sparse interval backend.
- Required proof artifacts include full and rollout-shaped PE workloads.
- Training proof exists before claiming train speedup.
- Validators reject skipped profiler checks, missing kernel markers, dense
  fallback events, failed correctness, and missing required cases.
- Representative PE workloads show material speedup over dense masked SDPA.
- Proof commands are documented and reproducible from the PE repo with `uv`.
