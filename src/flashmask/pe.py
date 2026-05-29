"""PE state-causal mask compiler.

The functions here mirror PE's dense attention policy without importing PE or
Torch. They accept plain sequences or any object with ``tolist()``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .core import IntervalMask, MaskNotRepresentableError


@dataclass(frozen=True)
class PETokenTypeIds:
    """Token type ids used by PE's next-state tokenizer."""

    pad: int = 0
    bos: int = 1
    domain: int = 2
    state: int = 3


DEFAULT_PE_TOKEN_TYPES = PETokenTypeIds()
NO_STATE_TIME = -1


def compile_pe_state_causal_mask(
    token_type_id: Any,
    time_index: Any,
    valid_token: Any | None = None,
    *,
    mask_heads: int = 1,
    token_types: PETokenTypeIds = DEFAULT_PE_TOKEN_TYPES,
) -> IntervalMask:
    """Compile PE full-sequence metadata to FlashMask intervals.

    Inputs have shape ``[B,T]`` or ``[T]``. The output is a non-causal
    ``bound_num=2`` interval mask whose dense reconstruction exactly matches
    PE's dense state-causal policy for the same metadata.
    """

    return compile_pe_state_causal_query_mask(
        token_type_id,
        time_index,
        token_type_id,
        time_index,
        valid_token,
        mask_heads=mask_heads,
        token_types=token_types,
    )


def compile_pe_state_causal_query_mask(
    query_token_type_id: Any,
    query_time_index: Any,
    key_token_type_id: Any,
    key_time_index: Any,
    key_valid_token: Any | None = None,
    *,
    mask_heads: int = 1,
    token_types: PETokenTypeIds = DEFAULT_PE_TOKEN_TYPES,
) -> IntervalMask:
    """Compile PE query/key metadata for incremental attention blocks."""

    query_type = _normalize_2d_int("query_token_type_id", query_token_type_id)
    query_time = _normalize_2d_int("query_time_index", query_time_index)
    key_type = _normalize_2d_int("key_token_type_id", key_token_type_id)
    key_time = _normalize_2d_int("key_time_index", key_time_index)
    _check_same_shape("query_token_type_id", query_type, "query_time_index", query_time)
    _check_same_shape("key_token_type_id", key_type, "key_time_index", key_time)
    if len(query_type) != len(key_type):
        raise ValueError(
            f"query/key batch sizes must match, got {len(query_type)} and {len(key_type)}"
        )
    mask_heads = int(mask_heads)
    if mask_heads <= 0:
        raise ValueError(f"mask_heads must be positive, got {mask_heads}")

    key_valid = (
        _token_type_valid(key_type, token_types)
        if key_valid_token is None
        else _normalize_2d_bool("key_valid_token", key_valid_token)
    )
    _check_same_shape("key_token_type_id", key_type, "key_valid_token", key_valid)

    startend = []
    for batch_idx in range(len(query_type)):
        head_bounds = []
        for key_idx, current_key_type in enumerate(key_type[batch_idx]):
            head_bounds.append(
                _pe_query_interval_for_key(
                    query_type=query_type[batch_idx],
                    query_time=query_time[batch_idx],
                    key_type=current_key_type,
                    key_time=key_time[batch_idx][key_idx],
                    key_valid=key_valid[batch_idx][key_idx],
                    token_types=token_types,
                )
            )
        startend.append(tuple(tuple(head_bounds) for _ in range(mask_heads)))
    return IntervalMask(tuple(startend), causal=False, seqlen_q=len(query_type[0]))


def dense_pe_state_causal_mask(
    token_type_id: Any,
    time_index: Any,
    valid_token: Any | None = None,
    *,
    token_types: PETokenTypeIds = DEFAULT_PE_TOKEN_TYPES,
) -> list[list[list[bool]]]:
    """Return PE's full dense allowed mask with shape ``[B,T,T]``."""

    token_type = _normalize_2d_int("token_type_id", token_type_id)
    times = _normalize_2d_int("time_index", time_index)
    _check_same_shape("token_type_id", token_type, "time_index", times)
    valid = (
        _token_type_valid(token_type, token_types)
        if valid_token is None
        else _normalize_2d_bool("valid_token", valid_token)
    )
    _check_same_shape("token_type_id", token_type, "valid_token", valid)

    return dense_pe_state_causal_query_mask(
        token_type,
        times,
        token_type,
        times,
        valid,
        token_types=token_types,
    )


def dense_pe_state_causal_query_mask(
    query_token_type_id: Any,
    query_time_index: Any,
    key_token_type_id: Any,
    key_time_index: Any,
    key_valid_token: Any | None = None,
    *,
    token_types: PETokenTypeIds = DEFAULT_PE_TOKEN_TYPES,
) -> list[list[list[bool]]]:
    """Return PE's dense allowed query/key mask with shape ``[B,Q,K]``."""

    query_type = _normalize_2d_int("query_token_type_id", query_token_type_id)
    query_time = _normalize_2d_int("query_time_index", query_time_index)
    key_type = _normalize_2d_int("key_token_type_id", key_token_type_id)
    key_time = _normalize_2d_int("key_time_index", key_time_index)
    _check_same_shape("query_token_type_id", query_type, "query_time_index", query_time)
    _check_same_shape("key_token_type_id", key_type, "key_time_index", key_time)
    if len(query_type) != len(key_type):
        raise ValueError(
            f"query/key batch sizes must match, got {len(query_type)} and {len(key_type)}"
        )

    valid = (
        _token_type_valid(key_type, token_types)
        if key_valid_token is None
        else _normalize_2d_bool("key_valid_token", key_valid_token)
    )
    _check_same_shape("key_token_type_id", key_type, "key_valid_token", valid)

    dense = []
    for batch_idx in range(len(query_type)):
        batch_mask = []
        for q_idx, q_type in enumerate(query_type[batch_idx]):
            row = []
            q_time = query_time[batch_idx][q_idx]
            for k_idx, k_type in enumerate(key_type[batch_idx]):
                row.append(
                    _pe_allows(
                        query_type=q_type,
                        query_time=q_time,
                        key_type=k_type,
                        key_time=key_time[batch_idx][k_idx],
                        key_valid=valid[batch_idx][k_idx],
                        token_types=token_types,
                    )
                )
            batch_mask.append(row)
        dense.append(batch_mask)
    return dense


def _pe_allows(
    *,
    query_type: int,
    query_time: int,
    key_type: int,
    key_time: int,
    key_valid: bool,
    token_types: PETokenTypeIds,
) -> bool:
    if not key_valid:
        return False

    key_is_bos = key_type == token_types.bos
    key_is_domain = key_type == token_types.domain
    key_is_special = key_is_bos or key_is_domain
    key_is_state = key_type == token_types.state

    if query_type == token_types.state:
        return key_is_special or (key_is_state and key_time <= query_time)
    if query_type == token_types.bos:
        return key_is_bos
    if query_type == token_types.domain:
        return key_is_bos or key_is_domain
    return False


def _pe_query_interval_for_key(
    *,
    query_type: list[int],
    query_time: list[int],
    key_type: int,
    key_time: int,
    key_valid: bool,
    token_types: PETokenTypeIds,
) -> tuple[int, int]:
    start: int | None = None
    end: int | None = None
    closed = False
    for query_idx, current_query_type in enumerate(query_type):
        allowed = _pe_allows(
            query_type=current_query_type,
            query_time=query_time[query_idx],
            key_type=key_type,
            key_time=key_time,
            key_valid=key_valid,
            token_types=token_types,
        )
        if allowed:
            if closed:
                raise MaskNotRepresentableError(
                    "PE query/key metadata produces a non-contiguous allowed "
                    f"query interval for key_type={key_type}, key_time={key_time}"
                )
            if start is None:
                start = query_idx
            end = query_idx + 1
        elif start is not None:
            closed = True

    if start is None or end is None:
        return (0, 0)
    return (end, start)


def _normalize_2d_int(name: str, value: Any) -> list[list[int]]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{name} must be a 1D or 2D sequence")
    if value and all(not isinstance(item, (list, tuple)) for item in value):
        value = [value]
    if not value:
        raise ValueError(f"{name} must be non-empty")
    out = []
    width: int | None = None
    for row in value:
        if not isinstance(row, (list, tuple)):
            raise TypeError(f"{name} must be a 1D or 2D sequence")
        if width is None:
            width = len(row)
            if width == 0:
                raise ValueError(f"{name} rows must be non-empty")
        elif len(row) != width:
            raise ValueError(f"{name} must not be ragged")
        out.append([int(item) for item in row])
    return out


def _normalize_2d_bool(name: str, value: Any) -> list[list[bool]]:
    rows = _normalize_2d_int(name, value)
    return [[bool(item) for item in row] for row in rows]


def _check_same_shape(
    left_name: str,
    left: list[list[Any]],
    right_name: str,
    right: list[list[Any]],
) -> None:
    left_shape = (len(left), len(left[0]) if left else 0)
    right_shape = (len(right), len(right[0]) if right else 0)
    if left_shape != right_shape:
        raise ValueError(f"{right_name} shape {right_shape} must match {left_name} shape {left_shape}")


def _token_type_valid(
    token_type_id: list[list[int]],
    token_types: PETokenTypeIds,
) -> list[list[bool]]:
    return [[token_type != token_types.pad for token_type in row] for row in token_type_id]
