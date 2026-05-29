from __future__ import annotations

import sys
from pathlib import Path

import pytest

from flashmask import (
    NO_STATE_TIME,
    PETokenTypeIds,
    compile_pe_state_causal_mask,
    compile_pe_state_causal_query_mask,
    dense_pe_state_causal_mask,
)


TYPES = PETokenTypeIds()


def _heads_to_batch(mask):
    return [batch[0] for batch in mask.to_bool_mask()]


def test_pe_compiler_matches_expected_state_causal_policy():
    token_type_id = [
        [TYPES.bos, TYPES.domain, TYPES.state, TYPES.state, TYPES.pad],
        [TYPES.bos, TYPES.domain, TYPES.state, TYPES.pad, TYPES.pad],
    ]
    time_index = [
        [NO_STATE_TIME, NO_STATE_TIME, 1, 2, NO_STATE_TIME],
        [NO_STATE_TIME, NO_STATE_TIME, 1, NO_STATE_TIME, NO_STATE_TIME],
    ]
    valid_token = [
        [True, True, True, True, False],
        [True, True, True, False, False],
    ]

    mask = compile_pe_state_causal_mask(token_type_id, time_index, valid_token)

    assert mask.shape == (2, 1, 5, 2)
    assert mask.causal is False
    assert _heads_to_batch(mask) == [
        [
            [True, False, False, False, False],
            [True, True, False, False, False],
            [True, True, True, False, False],
            [True, True, True, True, False],
            [False, False, False, False, False],
        ],
        [
            [True, False, False, False, False],
            [True, True, False, False, False],
            [True, True, True, False, False],
            [False, False, False, False, False],
            [False, False, False, False, False],
        ],
    ]


def test_pe_compiler_preserves_same_timestep_state_visibility():
    token_type_id = [[TYPES.bos, TYPES.domain, TYPES.state, TYPES.state]]
    time_index = [[NO_STATE_TIME, NO_STATE_TIME, 1, 1]]

    dense = dense_pe_state_causal_mask(token_type_id, time_index)
    mask = compile_pe_state_causal_mask(token_type_id, time_index)

    assert dense[0][2][3] is True
    assert dense[0][3][2] is True
    assert _heads_to_batch(mask) == dense


def test_pe_query_compiler_supports_incremental_blocks():
    query_type = [[TYPES.state, TYPES.state]]
    query_time = [[1, 2]]
    key_type = [[TYPES.bos, TYPES.domain, TYPES.state, TYPES.state]]
    key_time = [[NO_STATE_TIME, NO_STATE_TIME, 1, 2]]
    key_valid = [[True, True, True, True]]

    mask = compile_pe_state_causal_query_mask(
        query_type,
        query_time,
        key_type,
        key_time,
        key_valid,
    )

    assert mask.shape == (1, 1, 4, 2)
    assert mask.seqlen_q == 2
    assert mask.to_bool_mask()[0][0] == [
        [True, True, True, False],
        [True, True, True, True],
    ]


def test_optional_pe_dense_policy_parity_when_pe_and_torch_are_available():
    pytest.importorskip("torch")
    pe_root = Path("/home/jake/Developer/pe")
    if not (pe_root / "components" / "batch.py").exists():
        pytest.skip("PE checkout is not available")

    sys.path.insert(0, str(pe_root))
    try:
        from components import batch, tokenizer
    finally:
        sys.path.remove(str(pe_root))

    torch = pytest.importorskip("torch")
    token_type_id = torch.tensor(
        [[
            tokenizer.TOKEN_TYPE_BOS_ID,
            tokenizer.TOKEN_TYPE_DOMAIN_ID,
            tokenizer.TOKEN_TYPE_STATE_ID,
            tokenizer.TOKEN_TYPE_STATE_ID,
        ]],
        dtype=torch.long,
    )
    time_index = torch.tensor(
        [[tokenizer.NO_STATE_TIME, tokenizer.NO_STATE_TIME, 1, 1]],
        dtype=torch.long,
    )
    valid_token = torch.ones_like(token_type_id, dtype=torch.bool)

    pe_dense = batch.build_state_causal_attention_mask_batch(
        time_index,
        token_type_id,
        valid_token,
    ).tolist()
    flashmask_dense = _heads_to_batch(
        compile_pe_state_causal_mask(
            token_type_id.tolist(),
            time_index.tolist(),
            valid_token.tolist(),
        )
    )

    assert flashmask_dense == pe_dense

