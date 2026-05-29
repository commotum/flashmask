# Phase 8: Hardening

## Pasteable Goal

Harden FlashMask into a usable standalone dependency with clear docs, packaging,
build modes, dependency audits, hard-gated GPU tests, actionable errors, and PE
readiness checks. The hardened current target is SM86/SM8x; SM90/Hopper should
remain documented as a templated, fail-closed path until H100/H200 proof exists.
See
`/home/jake/Developer/flashmask/goal/phase-8-hardening.md` for the detailed
scope, tests, and exit criteria.

## Objective

Make the package usable, maintainable, and auditable as a standalone dependency.

This phase turns the working implementation into something PE can depend on
without special knowledge of the porting process.

Hardening should not require Hopper hardware for current completion. It should
make the SM86/SM8x path production-usable and make the SM90/Hopper path obvious,
documented, and fail-closed until later runtime proof is recorded.

## Non-Goals

- Do not add new mask semantics or kernel features unless required to close a
  hardening gap.
- Do not hide unsupported configurations behind dense fallbacks.
- Do not make GPU tests mandatory for CPU-only development.
- Do not introduce large external training-framework dependencies.

## Documentation

Required docs:

- package overview and project boundary
- public Python API
- `IntervalMask` shape and semantics
- PE state-autoregressive compiler behavior
- structured mask constructors
- dense reconstruction helpers
- backend router behavior
- build modes
- GPU architecture support
- failure modes
- PE integration instructions
- proof/benchmark commands

Docs must clearly state:

- `flashmask` owns sparse masks and kernel-native attention
- PE owns experiment/model/train/eval policy
- `ankos` owns CA mechanics and rollout outputs
- Paddle/PaddleNLP are reference material, not runtime dependencies

## Packaging

Package artifacts should be intentionally small.

Required checks:

- source distribution contains needed CUDA/C++ sources
- wheel does not accidentally include huge reference repos, papers, cache files,
  or generated artifacts
- package metadata is accurate
- optional extension build does not run unless requested
- pure Python install works without CUDA
- stub/extension install modes are documented

Files to audit:

- `pyproject.toml`
- `MANIFEST.in`
- `setup.py`
- package `__init__.py`
- generated `*.egg-info`
- built wheel/sdist contents

## Build Modes

Document and test supported build modes:

- pure Python, no extension
- stub extension
- SM80/SM86 sparse interval extension, the current strict runtime target
- SM90 FA3-compatible template extension, including build flags, metadata,
  architecture checks, and deferred proof commands

Each build mode should say:

- required environment variables
- required CUDA/PyTorch/CUTLASS versions or paths
- expected GPU target
- supported dtypes/head dimensions
- whether forward is ready
- whether backward is ready
- expected failure if run on wrong hardware

## Dependency Audit

Runtime dependency policy:

- no Paddle
- no PaddleNLP
- no reference-repo imports
- no CUDA import at pure Python mask-construction time unless required
- PyTorch import should be lazy where practical for pure mask utilities

Audit methods:

- import tests
- static source search for forbidden imports
- package install tests in a clean environment
- wheel/sdist inspection

Forbidden runtime imports should fail tests.

## Test Matrix

CPU-safe tests:

- mask representation
- dense reconstruction
- PE compiler semantics
- structured masks
- backend router fail-closed behavior
- package import
- dependency audit
- packaging metadata

Optional GPU tests:

- SM80/SM86 forward/backward/parity/profiler
- SM90 forward/backward/parity/profiler as deferred hard-gated Hopper tests
- raw op smoke tests
- PE integration parity
- proof validator on generated artifacts

Tests should be split so:

- `uv run pytest -q` works on a CPU-only or no-extension machine
- explicit env vars make GPU suites fail loud instead of skip silently
- GPU tests record enough information to debug backend selection

## Error Message Quality

Every common failure should be actionable:

- missing extension
- wrong GPU architecture
- backend not compiled
- missing CUTLASS path
- unsupported dtype
- unsupported head dimension
- unsupported GQA
- missing backward
- invalid mask shape or dtype
- dense mask passed to FlashMask backend
- profiler proof missing expected kernel markers

Errors should include:

- requested backend
- selected backend, if any
- active compute capability
- compiled backend kind
- relevant build flag or command hint when known

## CI And Local Workflow

Recommended local commands:

```bash
uv run pytest -q
```

Packaging smoke:

```bash
uv build
```

Optional GPU commands should remain documented per architecture and should not
be required for ordinary CPU-side development.

If CI is added later, split jobs by capability:

- CPU/package job
- SM80/SM86 GPU job
- SM90 GPU job, deferred unless Hopper CI/hardware is available
- PE integration job

## Artifact Hygiene

Generated artifacts should not be committed unless intentionally versioned:

- benchmark JSONL
- profiler traces
- build directories
- egg-info
- CUDA object files
- temporary copied reference outputs

Docs should point to artifact locations under `artifacts/`, but `.gitignore`
should keep generated proof files out unless explicitly needed.

## API Stability

Before declaring hardening complete:

- public API names are finalized or clearly marked experimental
- backend names and aliases are documented
- error types are documented
- dense reconstruction semantics are documented
- versioning expectations are documented

Breaking changes after this phase should require deliberate migration notes.

## PE Readiness

PE should be able to:

- install `flashmask` through editable `uv` dependency
- import the package without Paddle/PaddleNLP
- compile PE metadata into `IntervalMask`
- call the public FlashMask attention API
- verify selected backend readiness
- run dense parity tests
- run hard-gated GPU proof tests

PE should not require internal CUDA source knowledge.

## Release Checklist

Before considering the package ready for regular use:

- CPU-safe tests pass
- package build passes
- dependency audit passes
- docs cover install/build/use/failure modes
- SM80/SM86 proof passes on supported hardware
- SM90 proof passes on SM90 hardware before claiming Hopper support, but this
  is a deferred validation item rather than a blocker for current SM86
  hardening
- PE parity passes
- PE benchmark proof passes
- backward/training readiness is accurately reported
- no known dense fallback exists in FlashMask backends

If some backend remains inference-only, docs and runtime checks must say so
clearly.

## Exit Criteria

- `uv run pytest -q` passes for CPU-safe tests.
- `uv build` produces expected package artifacts.
- Optional GPU suites are hard-gated and fail loud when explicitly required.
- The package installs, imports, tests, and runs without large external training
  frameworks.
- Runtime import audit proves there is no Paddle/PaddleNLP dependency.
- Docs explain the boundary between `flashmask`, `pe`, and `ankos`.
- Docs explain build modes, backend routing, and common failures.
- Docs clearly mark SM86/SM8x as the current strict runtime target and
  SM90/Hopper as deferred until hard-gated H100/H200 proof exists.
- PE can consume the package without architecture-specific implementation
  knowledge.
- Generated artifacts are ignored or intentionally documented.
