from __future__ import annotations

from dataclasses import dataclass

import pytest

import flashmask
import flashmask._backend as backend_module
from flashmask._backend import ExtensionStatus, sparse_attention_forward


@dataclass(frozen=True)
class FakeTensor:
    shape: tuple[int, ...]


def _force_ready_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        backend_module,
        "extension_status",
        lambda: ExtensionStatus(loaded=True, kernel_ready=True),
    )


def test_sparse_forward_rejects_causal_override_mismatch(monkeypatch):
    _force_ready_backend(monkeypatch)
    mask = flashmask.IntervalMask([[[[1]]]], causal=True, seqlen_q=1)
    q = FakeTensor((1, 1, 1, 1))

    with pytest.raises(ValueError, match="causal override"):
        sparse_attention_forward(q, None, None, mask, causal=False)


def test_sparse_forward_rejects_query_length_mismatch(monkeypatch):
    _force_ready_backend(monkeypatch)
    mask = flashmask.IntervalMask([[[[4]]]], causal=True, seqlen_q=5)
    q = FakeTensor((1, 4, 1, 1))

    with pytest.raises(ValueError, match="seqlen_q=5"):
        sparse_attention_forward(q, None, None, mask)


def test_sparse_forward_rejects_bounds_beyond_query_length(monkeypatch):
    _force_ready_backend(monkeypatch)
    mask = flashmask.IntervalMask([[[[5]]]], causal=True)
    q = FakeTensor((1, 4, 1, 1))

    with pytest.raises(ValueError, match="max bound 5 exceeds q sequence length 4"):
        sparse_attention_forward(q, None, None, mask)


def test_sparse_forward_rejects_causal_bound4_before_raw_op(monkeypatch):
    _force_ready_backend(monkeypatch)
    mask = flashmask.IntervalMask([[[[0, 0, 0, 0]]]], causal=True, seqlen_q=1)
    q = FakeTensor((1, 1, 1, 1))

    with pytest.raises(ValueError, match="causal IntervalMask requires bound_num 1 or 2"):
        sparse_attention_forward(q, None, None, mask)


def test_sparse_forward_rejects_unordered_causal_bound2_before_raw_op(monkeypatch):
    _force_ready_backend(monkeypatch)
    mask = flashmask.IntervalMask([[[[3, 1]]]], causal=True, seqlen_q=4)
    q = FakeTensor((1, 4, 1, 1))

    with pytest.raises(ValueError, match="bounds are not ordered"):
        sparse_attention_forward(q, None, None, mask)


def test_sparse_forward_rejects_unordered_bound2_before_raw_op(monkeypatch):
    _force_ready_backend(monkeypatch)
    mask = flashmask.IntervalMask([[[[1, 3]]]], causal=False, seqlen_q=4)
    q = FakeTensor((1, 4, 1, 1))

    with pytest.raises(ValueError, match="bounds are not ordered"):
        sparse_attention_forward(q, None, None, mask)


def test_sparse_forward_rejects_unordered_bound4_before_raw_op(monkeypatch):
    _force_ready_backend(monkeypatch)
    mask = flashmask.IntervalMask([[[[2, 0, 1, 3]]]], causal=False, seqlen_q=4)
    q = FakeTensor((1, 4, 1, 1))

    with pytest.raises(ValueError, match="bounds are not ordered"):
        sparse_attention_forward(q, None, None, mask)


def test_attention_rejects_conflicting_causal_aliases_before_backend_check():
    mask = flashmask.IntervalMask([[[[1]]]], causal=True, seqlen_q=1)

    with pytest.raises(ValueError, match="causal and is_causal disagree"):
        flashmask.flashmask_attention(
            None,
            None,
            None,
            mask,
            causal=True,
            is_causal=False,
        )


def test_attention_rejects_unknown_kwargs():
    mask = flashmask.IntervalMask([[[[1]]]], causal=True, seqlen_q=1)

    with pytest.raises(TypeError):
        flashmask.flashmask_attention(None, None, None, mask, dropout_p=0.1)
