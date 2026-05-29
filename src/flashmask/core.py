"""Core interval-mask representation and dense reference reconstruction."""

from __future__ import annotations

from dataclasses import dataclass
from math import inf
from typing import Any, Literal


BoundNum = Literal[1, 2, 4]
NestedStartEnd = tuple[tuple[tuple[tuple[int, ...], ...], ...], ...]


class MaskNotRepresentableError(ValueError):
    """Raised when a dense mask cannot be represented by FlashMask intervals."""


@dataclass(frozen=True)
class IntervalMask:
    """Column-wise interval mask from the FlashMask paper.

    ``startend_row_indices`` has canonical shape ``[B, mask_heads, K, bound_num]``.
    Bounds are row indices into the query axis. The dense reference helpers
    interpret the bounds with the paper's lower/upper interval convention.
    """

    startend_row_indices: NestedStartEnd
    causal: bool
    seqlen_q: int | None = None

    def __init__(
        self,
        startend_row_indices: Any,
        *,
        causal: bool,
        seqlen_q: int | None = None,
    ) -> None:
        indices, shape = normalize_startend_row_indices(startend_row_indices)
        if seqlen_q is not None and int(seqlen_q) < 0:
            raise ValueError(f"seqlen_q must be non-negative, got {seqlen_q}")
        object.__setattr__(self, "startend_row_indices", indices)
        object.__setattr__(self, "causal", bool(causal))
        object.__setattr__(self, "seqlen_q", None if seqlen_q is None else int(seqlen_q))
        object.__setattr__(self, "_shape", shape)
        object.__setattr__(self, "_max_bound", _max_bound(indices))

    @property
    def shape(self) -> tuple[int, int, int, int]:
        return self._shape  # type: ignore[attr-defined]

    @property
    def batch_size(self) -> int:
        return self.shape[0]

    @property
    def mask_heads(self) -> int:
        return self.shape[1]

    @property
    def seqlen_k(self) -> int:
        return self.shape[2]

    @property
    def bound_num(self) -> int:
        return self.shape[3]

    @property
    def max_bound(self) -> int:
        return self._max_bound  # type: ignore[attr-defined]

    def to_list(self) -> list[list[list[list[int]]]]:
        return [
            [[list(bounds) for bounds in head] for head in batch]
            for batch in self.startend_row_indices
        ]

    def to_bool_mask(
        self,
        *,
        seqlen_q: int | None = None,
        nheads: int | None = None,
    ) -> list[list[list[list[bool]]]]:
        return dense_bool_from_intervals(self, seqlen_q=seqlen_q, nheads=nheads)

    def to_additive_mask(
        self,
        *,
        seqlen_q: int | None = None,
        nheads: int | None = None,
        masked_value: float = -inf,
    ) -> list[list[list[list[float]]]]:
        return dense_additive_from_intervals(
            self,
            seqlen_q=seqlen_q,
            nheads=nheads,
            masked_value=masked_value,
        )


def normalize_startend_row_indices(
    value: Any,
) -> tuple[NestedStartEnd, tuple[int, int, int, int]]:
    """Validate and normalize ``[B, H, K, bound_num]`` integer bounds."""

    dtype = getattr(value, "dtype", None)
    if dtype is not None and "int32" not in str(dtype):
        raise TypeError(f"startend_row_indices must be int32, got dtype {dtype}")

    if hasattr(value, "tolist"):
        value = value.tolist()

    if not isinstance(value, (list, tuple)):
        raise TypeError("startend_row_indices must be a 4D array-like value")

    batch = []
    batch_size = len(value)
    if batch_size == 0:
        raise ValueError("startend_row_indices batch dimension must be non-empty")

    mask_heads: int | None = None
    seqlen_k: int | None = None
    bound_num: int | None = None

    for b_idx, batch_value in enumerate(value):
        if not isinstance(batch_value, (list, tuple)):
            raise TypeError(f"batch {b_idx} must contain mask-head rows")
        if mask_heads is None:
            mask_heads = len(batch_value)
            if mask_heads == 0:
                raise ValueError("mask_heads dimension must be non-empty")
        elif len(batch_value) != mask_heads:
            raise ValueError("ragged mask_heads dimension in startend_row_indices")

        heads = []
        for h_idx, head_value in enumerate(batch_value):
            if not isinstance(head_value, (list, tuple)):
                raise TypeError(f"batch {b_idx}, head {h_idx} must contain key rows")
            if seqlen_k is None:
                seqlen_k = len(head_value)
            elif len(head_value) != seqlen_k:
                raise ValueError("ragged seqlen_k dimension in startend_row_indices")

            keys = []
            for k_idx, bounds_value in enumerate(head_value):
                if not isinstance(bounds_value, (list, tuple)):
                    raise TypeError(
                        f"batch {b_idx}, head {h_idx}, key {k_idx} must contain bounds"
                    )
                if bound_num is None:
                    bound_num = len(bounds_value)
                    if bound_num not in (1, 2, 4):
                        raise ValueError(f"bound_num must be 1, 2, or 4, got {bound_num}")
                elif len(bounds_value) != bound_num:
                    raise ValueError("ragged bound_num dimension in startend_row_indices")

                bounds = tuple(_validate_index(value) for value in bounds_value)
                keys.append(bounds)
            heads.append(tuple(keys))
        batch.append(tuple(heads))

    assert mask_heads is not None
    assert seqlen_k is not None
    assert bound_num is not None
    return tuple(batch), (batch_size, mask_heads, seqlen_k, bound_num)


def dense_bool_from_intervals(
    mask: IntervalMask | Any,
    *,
    seqlen_q: int | None = None,
    nheads: int | None = None,
    causal: bool | None = None,
) -> list[list[list[list[bool]]]]:
    """Reconstruct an allowed-attention boolean mask.

    The result shape is ``[B, nheads, seqlen_q, seqlen_k]``. ``True`` means the
    query/key pair is allowed. When ``causal=True`` and query/key lengths differ,
    the causal triangle is bottom-right aligned, matching FlashAttention 2.1+
    and FlashAttention 3.
    """

    interval_mask = _as_interval_mask(mask, causal=causal, seqlen_q=seqlen_q)
    query_len = _resolve_seqlen_q(interval_mask, seqlen_q)
    output_heads = interval_mask.mask_heads if nheads is None else int(nheads)
    if output_heads <= 0:
        raise ValueError(f"nheads must be positive, got {output_heads}")
    if output_heads % interval_mask.mask_heads != 0:
        raise ValueError(
            f"nheads={output_heads} must be divisible by mask_heads={interval_mask.mask_heads}"
        )

    batch_size, mask_heads, seqlen_k, bound_num = interval_mask.shape
    dense = [
        [
            [[True for _ in range(seqlen_k)] for _ in range(query_len)]
            for _ in range(mask_heads)
        ]
        for _ in range(batch_size)
    ]

    for b_idx in range(batch_size):
        for h_idx in range(mask_heads):
            for key_idx in range(seqlen_k):
                bounds = interval_mask.startend_row_indices[b_idx][h_idx][key_idx]
                if interval_mask.causal:
                    causal_end = max(0, key_idx - (seqlen_k - query_len))
                    _mask_column_interval(dense[b_idx][h_idx], key_idx, 0, causal_end)

                    if bound_num == 2:
                        _mask_column_interval(
                            dense[b_idx][h_idx],
                            key_idx,
                            bounds[0],
                            bounds[1],
                        )
                    else:
                        _mask_column_interval(
                            dense[b_idx][h_idx],
                            key_idx,
                            bounds[0],
                            query_len,
                        )
                else:
                    if bound_num == 2:
                        _mask_column_interval(
                            dense[b_idx][h_idx],
                            key_idx,
                            bounds[0],
                            query_len,
                        )
                        _mask_column_interval(
                            dense[b_idx][h_idx],
                            key_idx,
                            0,
                            bounds[1],
                        )
                    elif bound_num == 4:
                        _mask_column_interval(
                            dense[b_idx][h_idx],
                            key_idx,
                            bounds[0],
                            bounds[1],
                        )
                        _mask_column_interval(
                            dense[b_idx][h_idx],
                            key_idx,
                            bounds[2],
                            bounds[3],
                        )
                    else:
                        raise ValueError("non-causal masks require bound_num 2 or 4")

    if output_heads == mask_heads:
        return dense

    repeat = output_heads // mask_heads
    return [
        [_copy_matrix(head) for head in batch_heads for _ in range(repeat)]
        for batch_heads in dense
    ]


def dense_additive_from_intervals(
    mask: IntervalMask | Any,
    *,
    seqlen_q: int | None = None,
    nheads: int | None = None,
    masked_value: float = -inf,
    causal: bool | None = None,
) -> list[list[list[list[float]]]]:
    """Reconstruct an additive attention bias with ``0`` for allowed entries."""

    dense_bool = dense_bool_from_intervals(
        mask,
        seqlen_q=seqlen_q,
        nheads=nheads,
        causal=causal,
    )
    return [
        [
            [
                [0.0 if allowed else float(masked_value) for allowed in row]
                for row in head
            ]
            for head in batch
        ]
        for batch in dense_bool
    ]


def compile_dense_bool_mask(
    allowed: Any,
    *,
    mask_heads: int = 1,
    bound_num: Literal["auto", 2, 4] = "auto",
) -> IntervalMask:
    """Compile a dense allowed mask into non-causal interval bounds.

    Accepts dense shapes ``[Q,K]``, ``[B,Q,K]``, or ``[B,H,Q,K]``. The compiler
    emits ``causal=False`` because arbitrary dense masks are already fully
    represented by the intervals. ``bound_num=2`` supports one contiguous
    allowed row interval per key column. ``bound_num=4`` supports up to two
    masked row intervals per key column.
    """

    dense = normalize_dense_bool_mask(allowed)
    dense = _repeat_dense_heads(dense, int(mask_heads))

    if bound_num not in ("auto", 2, 4):
        raise ValueError(f"bound_num must be 'auto', 2, or 4, got {bound_num!r}")

    if bound_num in ("auto", 2):
        try:
            return _compile_dense_bound2(dense)
        except MaskNotRepresentableError:
            if bound_num == 2:
                raise

    return _compile_dense_bound4(dense)


def normalize_dense_bool_mask(allowed: Any) -> list[list[list[list[bool]]]]:
    """Normalize ``[Q,K]``, ``[B,Q,K]``, or ``[B,H,Q,K]`` dense masks."""

    if hasattr(allowed, "tolist"):
        allowed = allowed.tolist()
    if not isinstance(allowed, (list, tuple)):
        raise TypeError("dense mask must be an array-like value")

    depth = _nested_depth(allowed)
    if depth == 2:
        return _validate_dense_shape([[_normalize_bool_matrix(allowed)]])
    if depth == 3:
        return _validate_dense_shape(
            [[_normalize_bool_matrix(batch)] for batch in allowed]
        )
    if depth == 4:
        return _validate_dense_shape([
            [_normalize_bool_matrix(head) for head in batch]
            for batch in allowed
        ])
    raise ValueError("dense mask must have shape [Q,K], [B,Q,K], or [B,H,Q,K]")


def _as_interval_mask(
    mask: IntervalMask | Any,
    *,
    causal: bool | None,
    seqlen_q: int | None,
) -> IntervalMask:
    if isinstance(mask, IntervalMask):
        if causal is not None and bool(causal) != mask.causal:
            raise ValueError("causal override does not match IntervalMask.causal")
        return mask
    if causal is None:
        raise TypeError("causal must be provided when mask is not an IntervalMask")
    return IntervalMask(mask, causal=causal, seqlen_q=seqlen_q)


def _resolve_seqlen_q(mask: IntervalMask, seqlen_q: int | None) -> int:
    if seqlen_q is not None:
        query_len = int(seqlen_q)
    elif mask.seqlen_q is not None:
        query_len = mask.seqlen_q
    else:
        query_len = mask.seqlen_k
    if query_len < 0:
        raise ValueError(f"seqlen_q must be non-negative, got {query_len}")
    return query_len


def _validate_index(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"bounds must be Python integers, got {value!r}")
    if value < 0:
        raise ValueError(f"bounds must be non-negative, got {value}")
    if value < -(2**31) or value > 2**31 - 1:
        raise ValueError(f"bounds must fit int32, got {value}")
    return int(value)


def _max_bound(indices: NestedStartEnd) -> int:
    return max(
        bound
        for batch in indices
        for head in batch
        for key in head
        for bound in key
    )


def _mask_column_interval(
    matrix: list[list[bool]],
    key_idx: int,
    start: int,
    end: int,
) -> None:
    query_len = len(matrix)
    start = max(0, min(query_len, int(start)))
    end = max(0, min(query_len, int(end)))
    if end <= start:
        return
    for row_idx in range(start, end):
        matrix[row_idx][key_idx] = False


def _nested_depth(value: Any) -> int:
    depth = 0
    current = value
    while isinstance(current, (list, tuple)):
        depth += 1
        if not current:
            break
        current = current[0]
    return depth


def _normalize_bool_matrix(matrix: Any) -> list[list[bool]]:
    if not isinstance(matrix, (list, tuple)):
        raise TypeError("dense mask matrix must be a sequence")
    query_len = len(matrix)
    key_len: int | None = None
    out = []
    for row in matrix:
        if not isinstance(row, (list, tuple)):
            raise TypeError("dense mask matrix rows must be sequences")
        if key_len is None:
            key_len = len(row)
        elif len(row) != key_len:
            raise ValueError("ragged dense mask key dimension")
        out.append([bool(value) for value in row])
    if query_len == 0:
        raise ValueError("dense mask query dimension must be non-empty")
    if key_len is None or key_len == 0:
        raise ValueError("dense mask key dimension must be non-empty")
    return out


def _validate_dense_shape(
    dense: list[list[list[list[bool]]]],
) -> list[list[list[list[bool]]]]:
    batch_size = len(dense)
    if batch_size == 0:
        raise ValueError("dense mask batch dimension must be non-empty")
    mask_heads = len(dense[0])
    if mask_heads == 0:
        raise ValueError("dense mask head dimension must be non-empty")
    query_len = len(dense[0][0])
    key_len = len(dense[0][0][0])
    for batch in dense:
        if len(batch) != mask_heads:
            raise ValueError("ragged dense mask head dimension")
        for head in batch:
            if len(head) != query_len:
                raise ValueError("ragged dense mask query dimension")
            for row in head:
                if len(row) != key_len:
                    raise ValueError("ragged dense mask key dimension")
    return dense


def _repeat_dense_heads(
    dense: list[list[list[list[bool]]]],
    mask_heads: int,
) -> list[list[list[list[bool]]]]:
    if mask_heads <= 0:
        raise ValueError(f"mask_heads must be positive, got {mask_heads}")
    out = []
    for batch in dense:
        if len(batch) == mask_heads:
            out.append(batch)
        elif len(batch) == 1:
            out.append([_copy_matrix(batch[0]) for _ in range(mask_heads)])
        else:
            raise ValueError(
                f"dense mask has {len(batch)} heads and cannot be repeated to {mask_heads}"
            )
    return out


def _copy_matrix(matrix: list[list[bool]]) -> list[list[bool]]:
    return [list(row) for row in matrix]


def _compile_dense_bound2(
    dense: list[list[list[list[bool]]]],
) -> IntervalMask:
    startend = []
    seqlen_q = _dense_query_len(dense)
    seqlen_k = _dense_key_len(dense)
    for batch in dense:
        batch_bounds = []
        for head in batch:
            head_bounds = []
            for key_idx in range(seqlen_k):
                column = [head[row_idx][key_idx] for row_idx in range(seqlen_q)]
                intervals = _true_intervals(column)
                if len(intervals) > 1:
                    raise MaskNotRepresentableError(
                        "dense mask column has multiple allowed intervals; "
                        "bound_num=2 cannot represent it"
                    )
                if intervals:
                    start, end = intervals[0]
                else:
                    start, end = 0, 0
                head_bounds.append((end, start))
            batch_bounds.append(tuple(head_bounds))
        startend.append(tuple(batch_bounds))
    return IntervalMask(tuple(startend), causal=False, seqlen_q=seqlen_q)


def _compile_dense_bound4(
    dense: list[list[list[list[bool]]]],
) -> IntervalMask:
    startend = []
    seqlen_q = _dense_query_len(dense)
    seqlen_k = _dense_key_len(dense)
    for batch in dense:
        batch_bounds = []
        for head in batch:
            head_bounds = []
            for key_idx in range(seqlen_k):
                column = [head[row_idx][key_idx] for row_idx in range(seqlen_q)]
                intervals = _false_intervals(column)
                if len(intervals) > 2:
                    raise MaskNotRepresentableError(
                        "dense mask column has more than two masked intervals; "
                        "bound_num=4 cannot represent it"
                    )
                padded = intervals + [(0, 0)] * (2 - len(intervals))
                head_bounds.append(
                    (padded[0][0], padded[0][1], padded[1][0], padded[1][1])
                )
            batch_bounds.append(tuple(head_bounds))
        startend.append(tuple(batch_bounds))
    return IntervalMask(tuple(startend), causal=False, seqlen_q=seqlen_q)


def _dense_query_len(dense: list[list[list[list[bool]]]]) -> int:
    return len(dense[0][0])


def _dense_key_len(dense: list[list[list[list[bool]]]]) -> int:
    return len(dense[0][0][0])


def _true_intervals(values: list[bool]) -> list[tuple[int, int]]:
    return _intervals(values, target=True)


def _false_intervals(values: list[bool]) -> list[tuple[int, int]]:
    return _intervals(values, target=False)


def _intervals(values: list[bool], *, target: bool) -> list[tuple[int, int]]:
    intervals = []
    start: int | None = None
    for idx, value in enumerate(values):
        if value is target and start is None:
            start = idx
        elif value is not target and start is not None:
            intervals.append((start, idx))
            start = None
    if start is not None:
        intervals.append((start, len(values)))
    return intervals
