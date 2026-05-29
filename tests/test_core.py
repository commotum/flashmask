from __future__ import annotations

import math

import pytest

from flashmask import (
    IntervalMask,
    MaskNotRepresentableError,
    compile_dense_bool_mask,
    dense_bool_from_intervals,
)


def test_causal_reference_uses_bottom_right_alignment_when_q_shorter_than_k():
    mask = IntervalMask([[[[2], [2], [2], [2], [2]]]], causal=True, seqlen_q=2)

    assert mask.to_bool_mask()[0][0] == [
        [True, True, True, True, False],
        [True, True, True, True, True],
    ]


def test_causal_reference_uses_bottom_right_alignment_when_q_longer_than_k():
    mask = IntervalMask([[[[5], [5]]]], causal=True, seqlen_q=5)

    assert mask.to_bool_mask()[0][0] == [
        [False, False],
        [False, False],
        [False, False],
        [True, False],
        [True, True],
    ]


def test_bound2_noncausal_reconstructs_single_allowed_interval():
    mask = IntervalMask([[[[3, 1], [0, 0], [4, 0]]]], causal=False, seqlen_q=4)

    assert dense_bool_from_intervals(mask)[0][0] == [
        [False, False, True],
        [True, False, True],
        [True, False, True],
        [False, False, True],
    ]

    additive = mask.to_additive_mask()[0][0]
    assert additive[0][0] == -math.inf
    assert additive[1][0] == 0.0


def test_dense_compiler_uses_bound4_for_two_masked_intervals():
    dense = [
        [True, True],
        [False, True],
        [True, False],
        [False, True],
    ]

    mask = compile_dense_bool_mask(dense)

    assert mask.bound_num == 4
    assert mask.to_bool_mask()[0][0] == dense


def test_dense_compiler_rejects_columns_with_more_than_two_masked_intervals():
    dense = [
        [False],
        [True],
        [False],
        [True],
        [False],
    ]

    with pytest.raises(MaskNotRepresentableError):
        compile_dense_bool_mask(dense)


def test_raw_indices_require_causal_flag_for_reconstruction():
    with pytest.raises(TypeError):
        dense_bool_from_intervals([[[[1]]]])

    assert dense_bool_from_intervals([[[[2], [2]]]], causal=True)[0][0] == [
        [True, False],
        [True, True],
    ]


def test_interval_mask_tracks_max_bound_for_backend_validation():
    mask = IntervalMask([[[[1, 0], [4, 2], [3, 0]]]], causal=False, seqlen_q=4)

    assert mask.max_bound == 4
