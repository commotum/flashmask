"""Convenience builders for common structured interval masks."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .core import IntervalMask, compile_dense_bool_mask


def from_dense_bool_mask(
    allowed: Any,
    *,
    mask_heads: int = 1,
    bound_num: int | str = "auto",
) -> IntervalMask:
    """Compile a dense allowed mask into intervals when it is expressible."""

    if bound_num not in ("auto", 2, 4):
        raise ValueError(f"bound_num must be 'auto', 2, or 4, got {bound_num!r}")
    return compile_dense_bool_mask(allowed, mask_heads=mask_heads, bound_num=bound_num)  # type: ignore[arg-type]


def causal_mask(
    seqlen: int,
    *,
    batch_size: int = 1,
    mask_heads: int = 1,
    valid_lengths: int | Sequence[int] | None = None,
) -> IntervalMask:
    """Build a standard causal mask, optionally with right padding excluded."""

    seqlen = _positive_int("seqlen", seqlen)
    batch_size = _positive_int("batch_size", batch_size)
    mask_heads = _positive_int("mask_heads", mask_heads)
    lengths = _valid_lengths(valid_lengths, batch_size, seqlen)

    startend = []
    for valid_len in lengths:
        key_bounds = [
            (valid_len if key_idx < valid_len else 0,)
            for key_idx in range(seqlen)
        ]
        startend.append(tuple(tuple(key_bounds) for _ in range(mask_heads)))
    return IntervalMask(tuple(startend), causal=True, seqlen_q=seqlen)


def sliding_window_mask(
    seqlen: int,
    window_size: int,
    *,
    batch_size: int = 1,
    mask_heads: int = 1,
    valid_lengths: int | Sequence[int] | None = None,
) -> IntervalMask:
    """Build a causal sliding-window mask.

    ``window_size`` counts the current key position. With ``window_size=3``,
    query row ``i`` can attend keys ``i-2`` through ``i``.
    """

    seqlen = _positive_int("seqlen", seqlen)
    window_size = _positive_int("window_size", window_size)
    batch_size = _positive_int("batch_size", batch_size)
    mask_heads = _positive_int("mask_heads", mask_heads)
    lengths = _valid_lengths(valid_lengths, batch_size, seqlen)

    startend = []
    for valid_len in lengths:
        key_bounds = []
        for key_idx in range(seqlen):
            if key_idx >= valid_len:
                key_bounds.append((0,))
            else:
                key_bounds.append((min(valid_len, key_idx + window_size),))
        startend.append(tuple(tuple(key_bounds) for _ in range(mask_heads)))
    return IntervalMask(tuple(startend), causal=True, seqlen_q=seqlen)


def document_mask(
    document_lengths: Sequence[int],
    *,
    causal: bool = False,
    batch_size: int = 1,
    mask_heads: int = 1,
) -> IntervalMask:
    """Build document-local attention intervals.

    For ``causal=False``, tokens attend bidirectionally within their document.
    For ``causal=True``, each key is visible only to later query rows in the
    same document. The returned FlashMask object still has ``causal=False`` so
    same-document bounds, not global positional causality, define the mask.
    """

    spans = _document_spans(document_lengths)
    seqlen = spans[-1][1] if spans else 0
    if seqlen <= 0:
        raise ValueError("document_lengths must contain at least one token")
    batch_size = _positive_int("batch_size", batch_size)
    mask_heads = _positive_int("mask_heads", mask_heads)

    key_bounds = []
    for start, end in spans:
        for key_idx in range(start, end):
            allowed_start = key_idx if causal else start
            key_bounds.append((end, allowed_start))

    startend = tuple(
        tuple(tuple(key_bounds) for _ in range(mask_heads))
        for _ in range(batch_size)
    )
    return IntervalMask(startend, causal=False, seqlen_q=seqlen)


def prefix_lm_mask(
    seqlen: int,
    prefix_length: int,
    *,
    batch_size: int = 1,
    mask_heads: int = 1,
) -> IntervalMask:
    """Build a prefix-LM mask where prefix queries are bidirectional."""

    seqlen = _positive_int("seqlen", seqlen)
    prefix_length = int(prefix_length)
    if prefix_length < 0 or prefix_length > seqlen:
        raise ValueError(f"prefix_length must be in [0, {seqlen}], got {prefix_length}")
    batch_size = _positive_int("batch_size", batch_size)
    mask_heads = _positive_int("mask_heads", mask_heads)

    key_bounds = []
    for key_idx in range(seqlen):
        if key_idx < prefix_length:
            key_bounds.append((seqlen, 0))
        else:
            key_bounds.append((seqlen, key_idx))

    startend = tuple(
        tuple(tuple(key_bounds) for _ in range(mask_heads))
        for _ in range(batch_size)
    )
    return IntervalMask(startend, causal=False, seqlen_q=seqlen)


def _positive_int(name: str, value: int) -> int:
    value = int(value)
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return value


def _valid_lengths(
    valid_lengths: int | Sequence[int] | None,
    batch_size: int,
    seqlen: int,
) -> tuple[int, ...]:
    if valid_lengths is None:
        lengths = (seqlen,) * batch_size
    elif isinstance(valid_lengths, int):
        lengths = (int(valid_lengths),) * batch_size
    else:
        lengths = tuple(int(length) for length in valid_lengths)
        if len(lengths) != batch_size:
            raise ValueError(
                f"valid_lengths must have {batch_size} entries, got {len(lengths)}"
            )
    for length in lengths:
        if length < 0 or length > seqlen:
            raise ValueError(f"valid lengths must be in [0, {seqlen}], got {length}")
    return lengths


def _document_spans(document_lengths: Sequence[int]) -> list[tuple[int, int]]:
    spans = []
    cursor = 0
    for length in document_lengths:
        length = int(length)
        if length <= 0:
            raise ValueError(f"document lengths must be positive, got {length}")
        spans.append((cursor, cursor + length))
        cursor += length
    return spans

