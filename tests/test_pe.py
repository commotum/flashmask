from __future__ import annotations

import sys
from pathlib import Path

import pytest

from flashmask.core import MaskNotRepresentableError
from flashmask import (
    NO_STATE_TIME,
    PETokenTypeIds,
    compile_pe_state_causal_mask,
    compile_pe_state_causal_query_mask,
    dense_pe_state_causal_mask,
    dense_pe_state_causal_query_mask,
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


def test_pe_compiler_matches_dense_reference_for_multibatch_mask_heads():
    token_type_id = [
        [
            TYPES.bos,
            TYPES.domain,
            TYPES.state,
            TYPES.state,
            TYPES.state,
            TYPES.state,
            TYPES.pad,
            TYPES.pad,
        ],
        [
            TYPES.bos,
            TYPES.domain,
            TYPES.state,
            TYPES.state,
            TYPES.state,
            TYPES.state,
            TYPES.state,
            TYPES.pad,
        ],
    ]
    time_index = [
        [NO_STATE_TIME, NO_STATE_TIME, 1, 1, 2, 3, NO_STATE_TIME, NO_STATE_TIME],
        [NO_STATE_TIME, NO_STATE_TIME, 1, 2, 2, 3, 4, NO_STATE_TIME],
    ]
    valid_token = [
        [True, True, True, True, True, False, False, False],
        [True, True, True, True, True, True, True, False],
    ]

    dense = dense_pe_state_causal_mask(token_type_id, time_index, valid_token)
    mask = compile_pe_state_causal_mask(
        token_type_id,
        time_index,
        valid_token,
        mask_heads=3,
    )
    reconstructed = mask.to_bool_mask(seqlen_q=len(token_type_id[0]), nheads=3)

    assert mask.shape == (2, 3, 8, 2)
    for batch_idx in range(2):
        for head_idx in range(3):
            assert reconstructed[batch_idx][head_idx] == dense[batch_idx]
    assert dense[0][2][3] is True
    assert dense[0][3][2] is True
    assert all(not row[5] for row in dense[0])
    assert dense[0][1] == [True, True, False, False, False, False, False, False]


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


def test_pe_query_compiler_matches_dense_reference_for_multibatch_mask_heads():
    query_type = [
        [TYPES.bos, TYPES.domain, TYPES.state, TYPES.state],
        [TYPES.domain, TYPES.state, TYPES.state, TYPES.state],
    ]
    query_time = [
        [NO_STATE_TIME, NO_STATE_TIME, 1, 2],
        [NO_STATE_TIME, 1, 2, 3],
    ]
    key_type = [
        [TYPES.bos, TYPES.domain, TYPES.state, TYPES.state, TYPES.state, TYPES.pad],
        [TYPES.bos, TYPES.domain, TYPES.state, TYPES.state, TYPES.state, TYPES.pad],
    ]
    key_time = [
        [NO_STATE_TIME, NO_STATE_TIME, 1, 2, 3, NO_STATE_TIME],
        [NO_STATE_TIME, NO_STATE_TIME, 1, 2, 3, NO_STATE_TIME],
    ]
    key_valid = [
        [True, True, True, True, False, False],
        [True, True, True, True, True, False],
    ]

    dense = dense_pe_state_causal_query_mask(
        query_type,
        query_time,
        key_type,
        key_time,
        key_valid,
    )
    mask = compile_pe_state_causal_query_mask(
        query_type,
        query_time,
        key_type,
        key_time,
        key_valid,
        mask_heads=2,
    )
    reconstructed = mask.to_bool_mask(seqlen_q=len(query_type[0]), nheads=2)

    assert mask.shape == (2, 2, 6, 2)
    assert mask.seqlen_q == 4
    for batch_idx in range(2):
        for head_idx in range(2):
            assert reconstructed[batch_idx][head_idx] == dense[batch_idx]
    assert dense[0][3][3] is True
    assert all(not row[4] for row in dense[0])
    assert all(not row[5] for batch_rows in dense for row in batch_rows)


def test_pe_query_compiler_rejects_non_contiguous_query_intervals():
    with pytest.raises(MaskNotRepresentableError):
        compile_pe_state_causal_query_mask(
            [[TYPES.state, TYPES.state, TYPES.state]],
            [[2, 1, 3]],
            [[TYPES.state]],
            [[2]],
            [[True]],
        )


def test_pe_public_compiler_does_not_build_dense_intermediate():
    source = (Path(__file__).resolve().parents[1] / "src" / "flashmask" / "pe.py").read_text()
    assert "compile_dense_bool_mask" not in source

    full_body = source.split("def compile_pe_state_causal_mask(", 1)[1].split(
        "\ndef compile_pe_state_causal_query_mask",
        1,
    )[0]
    query_body = source.split("def compile_pe_state_causal_query_mask(", 1)[1].split(
        "\ndef dense_pe_state_causal_mask",
        1,
    )[0]
    assert "dense_pe_state_causal_mask(" not in full_body
    assert "dense_pe_state_causal_query_mask(" not in query_body


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
