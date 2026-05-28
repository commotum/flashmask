**Direction**

Use `autoresearch` as a template, not as the codebase to rewrite in place. The current repo is tightly centered on one editable file and one scalar metric, `val_bpb`, as stated in [autoresearch/README.md](/home/jake/Developer/fm/autoresearch/README.md:11) and [autoresearch/program.md](/home/jake/Developer/fm/autoresearch/program.md:25). Step 1 needs a different contract: multi-file kernel work, frozen correctness harnesses, and staged goals from dense reference to interval-based sparse FlashMask.

I would create a new sibling repo, something like `flashmask-lab` or `maskresearch`, and keep `autoresearch` unchanged as the eventual consumer.

**What To Change**

Start by keeping the parts of `autoresearch` that are actually useful:
- `program.md` as the agent control plane.
- `results.tsv` logging and “keep/discard/crash” experiment bookkeeping.
- The autonomous branch-based loop.
- The small-repo philosophy.

Replace the training-specific parts:
- Remove the assumption that only one file is editable.
- Remove `prepare.py`, tokenizer/data download, and the `val_bpb` evaluation path.
- Remove the “5-minute train.py run” metric loop.
- Replace “improve training loss” with “preserve correctness, then improve backend capability/perf”.

Add a new fixed harness:
- `spec.md`: exact target behavior and supported mask families.
- `harness/reference_dense.py`: slow, obvious ground-truth implementation using dense masks.
- `harness/cases.py`: canonical mask fixtures and random property tests.
- `harness/eval.py`: correctness runner.
- `harness/bench.py`: throughput and memory runner.
- `results.tsv`: log correctness, perf, and supported features, not `val_bpb`.

Allow a controlled editable surface:
- `flashmask/abi.py`
- `flashmask/mask_spec.py`
- `flashmask/compiler.py`
- `flashmask/dense_backend.py`
- `flashmask/autoresearch_adapter.py`
- `flashmask/cuda/*` for the eventual extension
- optionally `tests/` if you want the agent to add regression tests, but keep the core harness human-owned

**Proposed Repo Shape**

```text
flashmask-lab/
  README.md
  program.md
  pyproject.toml
  spec.md
  flashmask/
    __init__.py
    abi.py
    mask_spec.py
    compiler.py
    dense_backend.py
    autoresearch_adapter.py
    cuda/
      bindings.py
      extension.cpp
      kernel_dense.cu
      kernel_interval.cu
  harness/
    reference_dense.py
    cases.py
    eval.py
    bench.py
  tests/
    test_reference.py
    test_compiler.py
    test_adapter.py
  results.tsv
```

**Execution Plan**

1. Define the target API first.
   The key decision is the seam between `autoresearch` and the new module. It should be neutral and PyTorch-friendly, not Paddle-shaped. `interface.md` already points at the real seam: sparse mask representation plus FlashAttention-style kernel ABI, not model code.

2. Stand up the dense reference before any CUDA work.
   Build a slow, exact dense-mask implementation that becomes the oracle for every later backend. This is the baseline the agent must never regress from.

3. Add an `autoresearch` adapter early.
   The new repo should expose one wrapper that `autoresearch` can eventually call without knowing about Paddle or PaddleNLP internals.

4. Make correctness the first gate.
   A candidate commit only “counts” if it passes the fixed correctness suite on supported mask families. Performance work starts only after that.

5. Add the dense accelerated backend.
   After the oracle exists, add a first backend with the same API but aimed at GPU execution. It can still be dense internally.

6. Add the column-interval compiler.
   Implement the FlashMask-style interval representation described in [FlashMask/summary_flashmask.md](/home/jake/Developer/fm/FlashMask/summary_flashmask.md:21), with explicit tests that dense and interval semantics match.

7. Add the sparse CUDA backend.
   Only once the compiler and dense oracle are stable should the agent iterate on tile classification, masked/unmasked block handling, and kernel performance.

8. Change `program.md` to a staged objective.
   Stage 0: dense oracle passes.
   Stage 1: adapter works in `autoresearch`.
   Stage 2: dense GPU backend passes.
   Stage 3: interval compiler passes.
   Stage 4: sparse CUDA backend passes and beats dense on targeted masks.

**Metrics To Log**

Replace the old `results.tsv` columns with something like:
`commit	stage	tests_pass	max_abs_err	max_rel_err	supported_masks	tokens_per_s	peak_mem_mb	status	description`

That gives the agent a sane optimization ladder:
- first correctness
- then feature coverage
- then speed/memory

If you want, I can turn this into a concrete rewritten `README.md` and `program.md` for the new repo next.
