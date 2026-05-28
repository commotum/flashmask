# 4D Data Prep + FlashMask API

This document specifies a concrete API for the hardest target setting:

- multi-domain
- 4D coordinates `(t, x, y, z)`
- state-autoregressive next-state prediction
- kernel-native sparse attention
- one shared interface for data prep, training, and inference

The design goal is:

1. keep **semantic structure explicit**
2. keep **execution layout efficient**
3. make dense and interval-mask backends interchangeable
4. support ARC, 0D+t, 1D+t, 2D+t, and 3D+t under one contract

## Principles

- The **canonical representation** stores only active tokens plus explicit metadata.
- The **execution representation** repacks tokens into contiguous fixed-size state blocks.
- Attention semantics are compiled from metadata, not inferred from flat token order.
- Training is **next-state**, not next-token, for spatial / structured domains.
- Inference rolls out **one state block at a time**.

## Core Concepts

### Domain

A domain defines the interpretation of tokens and coordinates.

Examples:

- `ARC`
- `SCALAR_0D_T`
- `CA_1D_T`
- `CA_2D_T`
- `CA_3D_T`

### Sequence

A sequence is one training or inference episode.

Examples:

- one ARC task with demonstrations and query
- one CA rollout
- one scalar recurrence rollout

Tokens from different sequences must never interact unless a policy explicitly allows it.

### State

A state is the atomic prediction unit.

Examples:

- ARC demo input grid
- ARC demo output grid
- ARC query input grid
- ARC query output grid
- one CA timestep
- one scalar timestep

The model predicts one next state from a context of previous states.

### Canonical Layout

The canonical layout is the semantic form:

- active tokens only
- explicit coordinates and metadata
- no unnecessary global padding

### Execution Layout

The execution layout is the kernel-facing form:

- tokens grouped into contiguous state blocks
- optional per-state block padding
- precomputed offsets for fast mask compilation

## Enumerations

```python
from enum import IntEnum


class DomainID(IntEnum):
    ARC = 0
    SCALAR_0D_T = 1
    CA_1D_T = 2
    CA_2D_T = 3
    CA_3D_T = 4


class RoleID(IntEnum):
    PREFIX = 0
    DEMO_INPUT = 1
    DEMO_OUTPUT = 2
    QUERY_INPUT = 3
    QUERY_OUTPUT = 4
    STATE_INPUT = 5
    STATE_TARGET = 6
    HEADER = 7
    SEP = 8
    PAD = 9


class MaskPolicyID(IntEnum):
    TOKEN_CAUSAL = 0
    STATE_CAUSAL_STRICT = 1
    STATE_CAUSAL_INCLUSIVE = 2
    ARC_DEMO_QUERY = 3
```

## Canonical Data Structures

### StateSpec

`StateSpec` describes one logical state before packing.

```python
from dataclasses import dataclass
from typing import Optional
import torch


@dataclass
class StateSpec:
    domain_id: int
    sequence_id: int
    state_idx: int
    role_id: int

    # Optional higher-level grouping
    example_idx: int = 0
    segment_id: int = 0

    # Geometry
    size_x: int = 1
    size_y: int = 1
    size_z: int = 1

    # Row-major active token payload for this state
    token_ids: torch.Tensor | None = None
    target_ids: Optional[torch.Tensor] = None

    # Local integer coordinates for each token in token_ids
    coord_x: Optional[torch.Tensor] = None
    coord_y: Optional[torch.Tensor] = None
    coord_z: Optional[torch.Tensor] = None

    # Loss and observation flags
    is_observed: bool = True
    is_target_state: bool = False
```

Notes:

- `token_ids` stores only active tokens for the state.
- For scalar 0D+t, the state has one token and `(x, y, z) = (0, 0, 0)`.
- For ARC, `size_x/size_y` are the local grid width/height, and `size_z = 1`.

### CanonicalSequence

`CanonicalSequence` is one episode as an ordered list of states.

```python
@dataclass
class CanonicalSequence:
    domain_id: int
    sequence_id: int
    states: list[StateSpec]
```

### CanonicalBatch

`CanonicalBatch` is a batch of episodes before execution packing.

```python
@dataclass
class CanonicalBatch:
    sequences: list[CanonicalSequence]
```

## Execution Layout

The execution layout is the packed, vectorized form consumed by training and attention backends.

### PackedTrajectory

```python
@dataclass
class PackedTrajectory:
    # Payload
    token_ids: torch.Tensor        # [T]
    target_ids: torch.Tensor       # [T]

    # Identity
    domain_id: torch.Tensor        # [T]
    sequence_id: torch.Tensor      # [T]
    state_idx: torch.Tensor        # [T]
    role_id: torch.Tensor          # [T]
    example_idx: torch.Tensor      # [T]
    segment_id: torch.Tensor       # [T]

    # Explicit 4D coordinates
    coord_t: torch.Tensor          # [T]
    coord_x: torch.Tensor          # [T]
    coord_y: torch.Tensor          # [T]
    coord_z: torch.Tensor          # [T]

    # Local state geometry
    size_x: torch.Tensor           # [T]
    size_y: torch.Tensor           # [T]
    size_z: torch.Tensor           # [T]

    # Masks
    valid_token: torch.Tensor      # [T] bool
    observed_token: torch.Tensor   # [T] bool
    loss_mask: torch.Tensor        # [T] bool

    # Block structure
    state_offsets: torch.Tensor    # [num_states + 1]
    state_block_size: int

    # Optional sequence block structure
    sequence_offsets: torch.Tensor # [num_sequences + 1]
```

### Semantics

- `token_ids[i]` is the input token for position `i`.
- `target_ids[i]` is the supervised target for position `i`, or `ignore_index` if `loss_mask[i]` is false.
- `coord_t/coord_x/coord_y/coord_z` are explicit coordinates used by positional encoding and mask compilation.
- `state_offsets` marks contiguous state blocks in the packed sequence.
- `state_block_size` is the execution block size, e.g. `128`.

## Packing API

### Canonical -> Execution

```python
def pack_canonical_batch(
    batch: CanonicalBatch,
    *,
    state_block_size: int,
    pad_token_id: int,
    ignore_index: int,
) -> PackedTrajectory:
    """
    Convert a canonical batch into a kernel-friendly packed trajectory.

    Rules:
    - preserve state order within each sequence
    - pack each state contiguously
    - optionally pad each state block to state_block_size
    - emit explicit metadata for every packed token
    """
    ...
```

### Domain Adapters

Each domain gets a canonical builder that emits `CanonicalSequence`.

```python
def build_arc_sequence(task: dict) -> CanonicalSequence:
    ...


def build_scalar_sequence(values: torch.Tensor) -> CanonicalSequence:
    ...


def build_ca_1d_sequence(states: torch.Tensor) -> CanonicalSequence:
    ...


def build_ca_2d_sequence(states: torch.Tensor) -> CanonicalSequence:
    ...


def build_ca_3d_sequence(states: torch.Tensor) -> CanonicalSequence:
    ...
```

## Positional Encoding Interface

Position encoding should consume explicit coordinates, not implicit packed index.

```python
@dataclass
class PositionInputs:
    coord_t: torch.Tensor   # [T]
    coord_x: torch.Tensor   # [T]
    coord_y: torch.Tensor   # [T]
    coord_z: torch.Tensor   # [T]
    role_id: torch.Tensor   # [T]
    domain_id: torch.Tensor # [T]
```

```python
def build_position_inputs(layout: PackedTrajectory) -> PositionInputs:
    ...
```

Supported families can include:

- flat 1D RoPE
- axial RoPE
- 4D axial RoPE
- MonSTER-style explicit coordinate encoding

The position module should ignore unused axes by setting them to zero.

## Mask Compilation

Mask semantics are separate from positional encoding.

### Dense Representation

```python
@dataclass
class DenseMask:
    allow: torch.Tensor  # [T, T] bool
```

### Interval Representation

```python
@dataclass
class IntervalMask:
    # FlashMask-style per-key metadata
    lt_start: torch.Tensor
    lt_end: torch.Tensor
    ut_start: torch.Tensor
    ut_end: torch.Tensor

    # Optional block summaries for kernel skipping
    block_meta: dict[str, torch.Tensor] | None = None
```

### Unified CompiledMask

```python
@dataclass
class CompiledMask:
    kind: str  # "dense" | "interval"
    dense: DenseMask | None = None
    interval: IntervalMask | None = None
```

### Compiler API

```python
def compile_mask(
    layout: PackedTrajectory,
    *,
    policy: MaskPolicyID,
    backend: str,
) -> CompiledMask:
    """
    Compile a dense or interval mask from explicit layout metadata.

    backend:
    - "dense"
    - "interval"
    """
    ...
```

### Required Policy Semantics

#### `TOKEN_CAUSAL`

- standard flat token autoregression
- mostly for legacy LM baselines

#### `STATE_CAUSAL_STRICT`

- token in state `s` may attend only to states `< s`
- useful for strict past-only dynamics

#### `STATE_CAUSAL_INCLUSIVE`

- token in state `s` may attend to states `<= s`
- use when a whole current state is visible while predicting the next state

#### `ARC_DEMO_QUERY`

Recommended ARC policy:

- observed demo/query states may attend within the observed prefix
- query output target slots may attend to all observed prefix states
- query output target slots may optionally attend within the same target state
- tokens from different sequences may never attend

## Attention Backend API

The attention backend consumes `q/k/v` and a compiled mask.

```python
def flashmask_attention(
    q: torch.Tensor,
    k: torch.Tensor,
    v: torch.Tensor,
    *,
    compiled_mask: CompiledMask,
    dropout_p: float = 0.0,
    scale: float | None = None,
) -> torch.Tensor:
    """
    Unified attention entry point.

    q, k, v shapes should follow the backend convention chosen by the model.
    """
    ...
```

The implementation should support:

- dense reference path
- interval / FlashMask path
- later backend specialization (CUDA, Triton, FlexAttention adapter)

## Training API

### Build Training Batch

```python
def build_training_batch(
    sequences: list[CanonicalSequence],
    *,
    state_block_size: int,
    pad_token_id: int,
    ignore_index: int,
) -> PackedTrajectory:
    ...
```

### Model Contract

```python
def forward_step(
    layout: PackedTrajectory,
    *,
    policy: MaskPolicyID,
    mask_backend: str,
) -> dict[str, torch.Tensor]:
    """
    Returns logits and any auxiliary outputs.
    """
    ...
```

### Loss Contract

```python
def compute_next_state_loss(
    logits: torch.Tensor,
    layout: PackedTrajectory,
    *,
    ignore_index: int,
) -> torch.Tensor:
    """
    Apply loss only where layout.loss_mask is true.
    """
    ...
```

Loss rules:

- target supervision is applied only on designated next-state tokens
- pads, separators, and observed prefix tokens must be ignored

## Inference API

Inference is statewise, not tokenwise.

### Known Next-State Shape

```python
def predict_next_state(
    prefix: CanonicalSequence,
    *,
    next_state_template: StateSpec,
    state_block_size: int,
    policy: MaskPolicyID,
    mask_backend: str,
) -> StateSpec:
    """
    Predict one next state when output geometry is already known.
    """
    ...
```

### Unknown Next-State Shape

```python
@dataclass
class PredictedShape:
    size_x: int
    size_y: int
    size_z: int


def predict_next_shape(
    prefix: CanonicalSequence,
    *,
    policy: MaskPolicyID,
    mask_backend: str,
) -> PredictedShape:
    ...
```

```python
def rollout_sequence(
    prefix: CanonicalSequence,
    *,
    num_future_states: int,
    predict_shape: bool,
    state_block_size: int,
    policy: MaskPolicyID,
    mask_backend: str,
) -> CanonicalSequence:
    """
    Append one full predicted state at a time.
    """
    ...
```

## Recommended Defaults

- Canonical format: active tokens only
- Execution format: contiguous state blocks with per-state block padding
- Default `state_block_size`: `128`
- Default training objective: next-state
- Default sparse policy for structured domains: `STATE_CAUSAL_INCLUSIVE`

For ARC specifically:

- use `ARC_DEMO_QUERY` as the default mask policy
- keep explicit `example_idx` and `role_id`
- avoid global `30x30` padding as the canonical representation
- if needed, provide a compatibility adapter for legacy `30x30` canvas layouts

## Why This API

This API is designed to support the hardest shared setting:

- 4D explicit coordinates
- statewise prediction
- structured masking
- multi-domain batching
- interchangeable dense and interval mask backends

In short:

- **data prep defines the structure**
- **mask compilation defines visibility**
- **position encoding defines geometry**
- **the kernel only executes the compiled policy**
