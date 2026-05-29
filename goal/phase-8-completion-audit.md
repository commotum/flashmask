# Phase 8 Completion Audit

Date: 2026-05-29

Goal reference:
`/home/jake/Developer/flashmask/goal/phase-8-hardening.md`.

## Status

Phase 8 is complete for the current SM86/SM8x hardening target.

SM90/Hopper remains a documented, fail-closed template target until H100/H200
runtime proof exists. SM80/A100 remains a separate deferred runtime-proof
target and is not claimed from local SM86/A6000 artifacts. Training speed is
not claimed without a separate train-step proof.

## Changes Landed

- Expanded `README.md` with:
  - project boundary between `flashmask`, PE, and `ankos`
  - public Python API and internal raw `torch.ops.flashmask.*` ABI boundary
  - pure Python, stub, SM8x, SM80-deferred, and SM90-template build modes
  - common failure modes and expected diagnostic fields
  - PE integration commands and proof commands
  - dependency and artifact hygiene policy
- Hardened public failure paths:
  - dense boolean/additive masks passed to `flashmask_attention` are rejected
    with a conversion hint
  - unsupported dtype and mismatched Q/K/V dtype fail before dispatch
  - backend failures include requested backend, selected backend, compiled
    backend kind, compute capability, readiness flags, CUDA availability, and
    build hints
- Hardened packaging/build setup:
  - experimental CUDA builds fail early with an actionable CUTLASS/CUTE header
    error before importing torch extension machinery
  - `MANIFEST.in` excludes `__pycache__` and bytecode from source artifacts
- Added regression coverage for the docs, dependency audit, build modes,
  failure diagnostics, and package artifact boundaries.

## Requirements And Evidence

- CPU-safe test matrix:
  - Command from `/home/jake/Developer/flashmask`:
    `uv run pytest -q`
  - Result: `135 passed, 26 skipped in 2.39s`.
  - Coverage includes mask representation, dense reconstruction, PE compiler
    semantics, structured masks, backend fail-closed behavior, package import,
    dependency audit, proof validation, and packaging metadata.

- Packaging smoke and artifact boundaries:
  - Command from `/home/jake/Developer/flashmask`:
    `uv build`
  - Result:
    `Successfully built dist/flashmask-0.1.0.tar.gz` and
    `Successfully built dist/flashmask-0.1.0-py3-none-any.whl`.
  - Inspection command:
    `tar -tzf dist/flashmask-0.1.0.tar.gz | rg '__pycache__|\.pyc|\.pyo|context/|documentation/|paper/|sub/|uv-docs|summary_flashmask' || true`
  - Result: no matches.
  - Wheel inspection result: no `flashmask/csrc/`, `__pycache__`, `.pyc`, or
    `.pyo` entries.
  - Focused package test:
    `uv run pytest -q tests/test_package_surface.py`
  - Result: `31 passed in 2.21s`.

- Clean pure-Python install:
  - Built wheel installed into a throwaway `uv venv` with `--no-index --no-deps`.
  - Import smoke blocked `torch`, Paddle, and PaddleNLP imports, imported
    `flashmask`, constructed an `IntervalMask`, reconstructed its dense bool
    mask, and verified `backend_info().available is False`.
  - Result: `clean wheel import ok`.

- Dependency audit:
  - Static package-surface test verifies runtime Python sources do not import
    Paddle, PaddleNLP, or reference `sub` packages.
  - Import tests verify `flashmask` import does not eagerly import torch,
    Paddle, or PaddleNLP.
  - `pyproject.toml` keeps runtime `dependencies = []`.

- Build modes:
  - `setup.py` supports mutually exclusive pure Python, stub,
    experimental SM8x, and experimental SM90 build modes.
  - Package-surface tests verify no torch import during normal metadata setup,
    mutually exclusive build env handling, SM80/SM86/SM90 instantiation lists,
    CUTLASS-header gating for experimental builds, and documented build
    commands.

- Error messages and fail-closed behavior:
  - Command from `/home/jake/Developer/flashmask`:
    `uv run pytest -q tests/test_backend_contract.py`
  - Result: `33 passed in 0.11s`.
  - Coverage includes unknown backend rejection, SM90 fail-closed proof gate,
    SM80 deferred proof gate, missing backward, dense-mask rejection,
    unsupported block-mask/GQA/head-dim/dtype checks, and backend diagnostic
    context.

- Optional GPU gates:
  - FlashMask optional tests skip by default for CPU/no-extension development
    and contain explicit `FLASHMASK_REQUIRE_SM86`, `FLASHMASK_REQUIRE_SM80`,
    `FLASHMASK_REQUIRE_SM8X`, and `FLASHMASK_REQUIRE_SM90` fail-loud gates.
  - Package tests assert these gates are present and do not use
    `pytest.importorskip("torch")` to hide required GPU runs.

- SM86 PE proof:
  - Command from `/home/jake/Developer/pe`:
    `PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu flashmask-validate-proof --backend fa2-compatible --min-speedup 1.5 --require-case full --require-case rollout artifacts/pe-flashmask-sm86.jsonl`
  - Result: `validated 2 FlashMask SM86 proof records`.
  - The validated proof set contains:
    `/home/jake/Developer/pe/artifacts/pe-flashmask-sm86-full.jsonl`,
    `/home/jake/Developer/pe/artifacts/pe-flashmask-sm86-rollout.jsonl`, and
    `/home/jake/Developer/pe/artifacts/pe-flashmask-sm86.jsonl`.

- PE readiness:
  - Focused command from `/home/jake/Developer/pe`:
    `PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu pytest -q tests/test_flashmask_verification_gates.py tests/test_flashmask_sm8x_gpu_parity.py`
  - Result: `22 passed in 18.09s`.
  - Full PE command from `/home/jake/Developer/pe`:
    `PYTHONPATH=/home/jake/Developer/flashmask/src uv run --extra gpu pytest -q`
  - Result: `120 passed, 10 skipped in 18.90s`.

- Proof validator hardening:
  - Covered by `tests/test_proof.py` in the full FlashMask suite.
  - Validator rejects missing profiler checks, missing kernel markers, dense
    fallback events, failed correctness/speed flags, missing required cases,
    invalid timing fields, invalid `backward_ready`, and insufficient
    `min_speedup`.

- Artifact hygiene:
  - `.gitignore` ignores generated build, dist, wheel, egg-info, Python cache,
    and shared-object outputs.
  - `MANIFEST.in` excludes reference directories, tests, summaries, bytecode,
    and cache directories while keeping CUDA/C++ source files in the sdist.

## Conclusion

FlashMask is now hardened as a standalone dependency for the current SM86/SM8x
target: documentation, packaging, dependency boundaries, build-mode checks,
fail-closed runtime behavior, proof validation, artifact hygiene, and PE
readiness are all covered by current tests and command evidence. Deferred
SM80/A100 and SM90/Hopper targets remain explicit and fail-closed until their
own hard-gated hardware proof exists.
