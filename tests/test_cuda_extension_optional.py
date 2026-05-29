from __future__ import annotations

import importlib.util
import math

import pytest

import flashmask


def test_compiled_extension_device_gate_when_present():
    if importlib.util.find_spec("flashmask._C") is None:
        pytest.skip("compiled FlashMask extension is not built")
    torch = pytest.importorskip("torch")
    if not torch.cuda.is_available():
        pytest.skip("CUDA is not available")

    import flashmask._C as extension

    capability = torch.cuda.get_device_capability()
    ready = bool(extension.kernel_ready())
    assert ready is (capability[0] == 9)

    q = torch.randn(1, 4, 1, 128, device="cuda", dtype=torch.float16)
    k = torch.randn(1, 4, 1, 128, device="cuda", dtype=torch.float16)
    v = torch.randn(1, 4, 1, 128, device="cuda", dtype=torch.float16)
    startend = torch.full((1, 1, 4, 1), 4, device="cuda", dtype=torch.int32)
    block_mask = torch.empty(0, device="cuda", dtype=torch.int32)

    if capability[0] != 9:
        with pytest.raises(RuntimeError, match="requires an SM90 GPU"):
            torch.ops.flashmask.fwd(q, k, v, startend, block_mask, float("nan"), True)
    else:
        out, lse = torch.ops.flashmask.fwd(
            q,
            k,
            v,
            startend,
            block_mask,
            float("nan"),
            True,
        )
        assert out.shape == q.shape
        assert lse.shape == (1, 1, 4)


def test_compiled_extension_debug_raw_validation_when_present(monkeypatch):
    if importlib.util.find_spec("flashmask._C") is None:
        pytest.skip("compiled FlashMask extension is not built")
    torch = pytest.importorskip("torch")
    if not torch.cuda.is_available():
        pytest.skip("CUDA is not available")

    import flashmask._C  # noqa: F401

    monkeypatch.setenv("FLASHMASK_VALIDATE_RAW_OP", "1")
    q = torch.randn(1, 4, 1, 128, device="cuda", dtype=torch.float16)
    k = torch.randn(1, 4, 1, 128, device="cuda", dtype=torch.float16)
    v = torch.randn(1, 4, 1, 128, device="cuda", dtype=torch.float16)
    startend = torch.full((1, 1, 4, 1), 5, device="cuda", dtype=torch.int32)
    block_mask = torch.empty(0, device="cuda", dtype=torch.int32)

    with pytest.raises(RuntimeError, match="startend bounds must be <= q sequence length"):
        torch.ops.flashmask.fwd(q, k, v, startend, block_mask, float("nan"), True)


def _require_sm90_raw_op():
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


def _dense_reference(torch, q, k, v, mask):
    scale = 1.0 / math.sqrt(q.size(-1))
    allowed = torch.tensor(mask.to_bool_mask(nheads=q.size(2)), device=q.device)
    scores = torch.einsum("bqhd,bkhd->bhqk", q.float(), k.float())
    scores = scores * scale
    scores = scores.masked_fill(~allowed, -torch.inf)
    probs = torch.softmax(scores, dim=-1)
    out = torch.einsum("bhqk,bkhd->bqhd", probs, v.float())
    return out, torch.logsumexp(scores, dim=-1), scale


@pytest.mark.parametrize(
    ("mask", "dtype_name"),
    [
        (flashmask.causal_mask(4), "float16"),
        (flashmask.causal_mask(4), "bfloat16"),
        (flashmask.document_mask([2, 2]), "float16"),
        (flashmask.document_mask([2, 2]), "bfloat16"),
    ],
)
def test_optional_sm90_raw_op_matches_dense_reference(mask, dtype_name):
    torch = _require_sm90_raw_op()
    dtype = getattr(torch, dtype_name)
    generator = torch.Generator(device="cuda").manual_seed(43)
    q = torch.randn(1, 4, 2, 128, device="cuda", dtype=dtype, generator=generator)
    k = torch.randn(1, 4, 2, 128, device="cuda", dtype=dtype, generator=generator)
    v = torch.randn(1, 4, 2, 128, device="cuda", dtype=dtype, generator=generator)
    startend = torch.as_tensor(mask.to_list(), device="cuda", dtype=torch.int32)
    block_mask = torch.empty(0, device="cuda", dtype=torch.int32)
    expected_out, expected_lse, scale = _dense_reference(torch, q, k, v, mask)

    out, lse = torch.ops.flashmask.fwd(
        q.contiguous(),
        k.contiguous(),
        v.contiguous(),
        startend,
        block_mask,
        scale,
        mask.causal,
    )

    atol = 6e-2 if dtype == torch.bfloat16 else 3e-2
    rtol = 6e-2 if dtype == torch.bfloat16 else 3e-2
    torch.testing.assert_close(out.float(), expected_out, atol=atol, rtol=rtol)
    torch.testing.assert_close(lse.float(), expected_lse, atol=atol, rtol=rtol)
