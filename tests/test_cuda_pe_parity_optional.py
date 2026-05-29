from __future__ import annotations

import importlib.util
import math

import pytest

import flashmask
from flashmask import (
    NO_STATE_TIME,
    PETokenTypeIds,
    compile_pe_state_causal_mask,
    compile_pe_state_causal_query_mask,
)


TYPES = PETokenTypeIds()


def _require_sm90_extension():
    if importlib.util.find_spec("flashmask._C") is None:
        pytest.skip("compiled FlashMask extension is not built")
    torch = pytest.importorskip("torch")
    if not torch.cuda.is_available():
        pytest.skip("CUDA is not available")

    import flashmask._C as extension

    capability = torch.cuda.get_device_capability()
    if capability[0] != 9 or not bool(extension.kernel_ready()):
        pytest.skip("FlashMask sparse forward requires an SM90 CUDA device")
    return torch


def _dense_attention_reference(torch, q, k, v, mask, *, softmax_scale: float):
    allowed = torch.tensor(mask.to_bool_mask(nheads=q.size(2)), device=q.device)
    scores = torch.einsum("bqhd,bkhd->bhqk", q.float(), k.float())
    scores = scores * softmax_scale
    scores = scores.masked_fill(~allowed, -torch.inf)
    probs = torch.softmax(scores, dim=-1)
    out = torch.einsum("bhqk,bkhd->bqhd", probs, v.float())
    softmax_lse = torch.logsumexp(scores, dim=-1)
    return out, softmax_lse


def _assert_flashmask_matches_dense(torch, q, k, v, mask):
    scale = 1.0 / math.sqrt(q.size(-1))
    result = flashmask.flashmask_attention(
        q.contiguous(),
        k.contiguous(),
        v.contiguous(),
        mask,
        softmax_scale=scale,
    )
    expected_out, expected_lse = _dense_attention_reference(
        torch,
        q,
        k,
        v,
        mask,
        softmax_scale=scale,
    )

    atol = 6e-2 if q.dtype == torch.bfloat16 else 3e-2
    rtol = 6e-2 if q.dtype == torch.bfloat16 else 3e-2
    torch.testing.assert_close(result.output.float(), expected_out, atol=atol, rtol=rtol)
    torch.testing.assert_close(
        result.softmax_lse.float(),
        expected_lse,
        atol=atol,
        rtol=rtol,
    )
    assert result.backend == "fa3"


@pytest.mark.parametrize("dtype_name", ["float16", "bfloat16"])
def test_optional_sm90_state_causal_full_sequence_matches_dense(dtype_name):
    torch = _require_sm90_extension()
    dtype = getattr(torch, dtype_name)
    generator = torch.Generator(device="cuda").manual_seed(17)

    token_type_id = [[TYPES.bos, TYPES.domain, TYPES.state, TYPES.state, TYPES.state]]
    time_index = [[NO_STATE_TIME, NO_STATE_TIME, 1, 1, 2]]
    mask = compile_pe_state_causal_mask(token_type_id, time_index, mask_heads=1)

    q = torch.randn(1, 5, 2, 128, device="cuda", dtype=dtype, generator=generator)
    k = torch.randn(1, 5, 2, 128, device="cuda", dtype=dtype, generator=generator)
    v = torch.randn(1, 5, 2, 128, device="cuda", dtype=dtype, generator=generator)

    assert mask.causal is False
    assert mask.bound_num == 2
    _assert_flashmask_matches_dense(torch, q, k, v, mask)


@pytest.mark.parametrize("dtype_name", ["float16", "bfloat16"])
def test_optional_sm90_state_causal_incremental_with_padded_key_matches_dense(dtype_name):
    torch = _require_sm90_extension()
    dtype = getattr(torch, dtype_name)
    generator = torch.Generator(device="cuda").manual_seed(29)

    mask = compile_pe_state_causal_query_mask(
        query_token_type_id=[[TYPES.state, TYPES.state]],
        query_time_index=[[1, 2]],
        key_token_type_id=[[TYPES.bos, TYPES.domain, TYPES.state, TYPES.state, TYPES.pad]],
        key_time_index=[[NO_STATE_TIME, NO_STATE_TIME, 1, 2, NO_STATE_TIME]],
        key_valid_token=[[True, True, True, True, False]],
        mask_heads=1,
    )

    q = torch.randn(1, 2, 2, 128, device="cuda", dtype=dtype, generator=generator)
    k = torch.randn(1, 5, 2, 128, device="cuda", dtype=dtype, generator=generator)
    v = torch.randn(1, 5, 2, 128, device="cuda", dtype=dtype, generator=generator)

    assert mask.causal is False
    assert mask.bound_num == 2
    assert mask.seqlen_q == 2
    _assert_flashmask_matches_dense(torch, q, k, v, mask)
