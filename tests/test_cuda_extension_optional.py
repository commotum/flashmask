from __future__ import annotations

import importlib.util
import math
import os

import pytest

import flashmask


def _require_or_skip(reason: str) -> None:
    if os.environ.get("FLASHMASK_REQUIRE_SM90") == "1":
        raise AssertionError(reason)
    pytest.skip(reason)


def _require_torch():
    try:
        import torch
    except Exception as exc:  # pragma: no cover - depends on local environment
        _require_or_skip(f"torch is required for FlashMask SM90 validation: {exc}")
    return torch


def test_compiled_extension_device_gate_when_present():
    if importlib.util.find_spec("flashmask._C") is None:
        _require_or_skip("compiled FlashMask extension is not built")
    torch = _require_torch()
    if not torch.cuda.is_available():
        _require_or_skip("CUDA is not available")

    import flashmask._C as extension

    capability = torch.cuda.get_device_capability()
    ready = bool(extension.kernel_ready())
    backend_kind = str(extension.backend_kind())
    if os.environ.get("FLASHMASK_REQUIRE_SM90") == "1" and (
        tuple(capability) != (9, 0)
        or backend_kind != flashmask.SPARSE_SM90_FA3_BACKEND_KIND
        or not ready
    ):
        raise AssertionError(
            "FlashMask sparse forward requires a ready SM90 / compute capability 9.0 "
            f"CUDA device, got {capability}"
        )
    if ready:
        assert (
            tuple(capability) == (9, 0)
            and backend_kind == flashmask.SPARSE_SM90_FA3_BACKEND_KIND
        ) or (
            capability[0] == 8
            and backend_kind == flashmask.SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND
        )

    q = torch.randn(1, 4, 1, 128, device="cuda", dtype=torch.float16)
    k = torch.randn(1, 4, 1, 128, device="cuda", dtype=torch.float16)
    v = torch.randn(1, 4, 1, 128, device="cuda", dtype=torch.float16)
    if backend_kind == flashmask.SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND:
        startend = torch.zeros((1, 1, 4, 2), device="cuda", dtype=torch.int32)
        startend[..., 0] = 4
        causal = False
    else:
        startend = torch.full((1, 1, 4, 1), 4, device="cuda", dtype=torch.int32)
        causal = True
    block_mask = torch.empty(0, device="cuda", dtype=torch.int32)

    if not ready:
        with pytest.raises(RuntimeError, match="requires .*compute capability|not implemented"):
            torch.ops.flashmask.fwd(q, k, v, startend, block_mask, float("nan"), causal)
    else:
        out, lse = torch.ops.flashmask.fwd(
            q,
            k,
            v,
            startend,
            block_mask,
            float("nan"),
            causal,
        )
        assert out.shape == q.shape
        assert lse.shape == (1, 1, 4)


def test_compiled_extension_debug_raw_validation_when_present(monkeypatch):
    if importlib.util.find_spec("flashmask._C") is None:
        _require_or_skip("compiled FlashMask extension is not built")
    torch = _require_torch()
    if not torch.cuda.is_available():
        _require_or_skip("CUDA is not available")

    import flashmask._C as extension

    monkeypatch.setenv("FLASHMASK_VALIDATE_RAW_OP", "1")
    q = torch.randn(1, 4, 1, 128, device="cuda", dtype=torch.float16)
    k = torch.randn(1, 4, 1, 128, device="cuda", dtype=torch.float16)
    v = torch.randn(1, 4, 1, 128, device="cuda", dtype=torch.float16)
    if extension.backend_kind() == flashmask.SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND:
        startend = torch.zeros((1, 1, 4, 2), device="cuda", dtype=torch.int32)
        startend[..., 0] = 5
        causal = False
    else:
        startend = torch.full((1, 1, 4, 1), 5, device="cuda", dtype=torch.int32)
        causal = True
    block_mask = torch.empty(0, device="cuda", dtype=torch.int32)

    with pytest.raises(RuntimeError) as exc_info:
        torch.ops.flashmask.fwd(q, k, v, startend, block_mask, float("nan"), causal)
    message = str(exc_info.value)
    if "not implemented" in message:
        _require_or_skip("compiled FlashMask extension is a stub build")
    assert "startend bounds must be <= q sequence length" in message


def _require_sm90_raw_op():
    if importlib.util.find_spec("flashmask._C") is None:
        _require_or_skip("compiled FlashMask extension is not built")
    torch = _require_torch()
    if not torch.cuda.is_available():
        _require_or_skip("CUDA is not available")

    import flashmask._C as extension

    capability = torch.cuda.get_device_capability()
    if tuple(capability) != (9, 0) or not bool(extension.kernel_ready()):
        _require_or_skip(
            "FlashMask sparse forward requires an SM90 / compute capability 9.0 CUDA device"
        )
    return torch


def _require_sm8x_raw_op():
    if importlib.util.find_spec("flashmask._C") is None:
        _require_or_skip("compiled FlashMask extension is not built")
    torch = _require_torch()
    if not torch.cuda.is_available():
        _require_or_skip("CUDA is not available")

    import flashmask._C as extension

    capability = torch.cuda.get_device_capability()
    if capability[0] != 8 or not bool(extension.kernel_ready()):
        _require_or_skip("FlashMask SM8x sparse forward requires a compute capability 8.x CUDA device")
    if extension.backend_kind() != flashmask.SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND:
        _require_or_skip("compiled FlashMask extension is not the SM8x sparse backend")
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


@pytest.mark.parametrize("dtype_name", ["float16", "bfloat16"])
def test_optional_sm8x_raw_op_matches_pe_dense_reference(dtype_name):
    torch = _require_sm8x_raw_op()
    dtype = getattr(torch, dtype_name)
    generator = torch.Generator(device="cuda").manual_seed(86)
    q = torch.randn(1, 6, 2, 64, device="cuda", dtype=dtype, generator=generator)
    k = torch.randn(1, 6, 2, 64, device="cuda", dtype=dtype, generator=generator)
    v = torch.randn(1, 6, 2, 64, device="cuda", dtype=dtype, generator=generator)
    mask = flashmask.compile_pe_state_causal_mask(
        token_type_id=[[1, 2, 3, 3, 3, 3]],
        time_index=[[-1, -1, 0, 0, 1, 2]],
        valid_token=[[True, True, True, True, True, True]],
        mask_heads=1,
    )
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


def test_optional_sm90_backward_path_fails_closed():
    torch = _require_sm90_raw_op()

    import flashmask._C as extension

    assert bool(extension.backward_ready()) is False
    with pytest.raises(RuntimeError, match="does not support backward"):
        flashmask.verify_backend(
            backend="fa3",
            require_fa3=True,
            require_sparse=True,
            require_backward=True,
        )


def test_optional_sm90_raw_op_rejects_nonempty_block_mask():
    torch = _require_sm90_raw_op()

    q = torch.randn(1, 4, 1, 128, device="cuda", dtype=torch.float16)
    k = torch.randn(1, 4, 1, 128, device="cuda", dtype=torch.float16)
    v = torch.randn(1, 4, 1, 128, device="cuda", dtype=torch.float16)
    startend = torch.full((1, 1, 4, 1), 4, device="cuda", dtype=torch.int32)
    block_mask = torch.zeros((1,), device="cuda", dtype=torch.int32)

    with pytest.raises(RuntimeError, match="does not support block_mask yet"):
        torch.ops.flashmask.fwd(q, k, v, startend, block_mask, float("nan"), True)


def test_optional_sm90_raw_op_rejects_native_gqa():
    torch = _require_sm90_raw_op()

    q = torch.randn(1, 4, 2, 128, device="cuda", dtype=torch.float16)
    k = torch.randn(1, 4, 1, 128, device="cuda", dtype=torch.float16)
    v = torch.randn(1, 4, 1, 128, device="cuda", dtype=torch.float16)
    startend = torch.full((1, 1, 4, 1), 4, device="cuda", dtype=torch.int32)
    block_mask = torch.empty(0, device="cuda", dtype=torch.int32)

    with pytest.raises(RuntimeError, match="requires q heads == kv heads"):
        torch.ops.flashmask.fwd(q, k, v, startend, block_mask, float("nan"), True)

    q = torch.randn(1, 4, 1, 128, device="cuda", dtype=torch.float16)
    k = torch.randn(1, 4, 1, 128, device="cuda", dtype=torch.float16)
    v = torch.randn(1, 4, 1, 128, device="cuda", dtype=torch.float16)
    out = torch.randn(1, 4, 1, 128, device="cuda", dtype=torch.float16)
    dout = torch.randn_like(out)
    softmax_lse = torch.randn(1, 1, 4, device="cuda", dtype=torch.float32)
    startend = torch.full((1, 1, 4, 1), 4, device="cuda", dtype=torch.int32)
    block_mask = torch.empty(0, device="cuda", dtype=torch.int32)

    with pytest.raises(RuntimeError, match="backward kernel is not implemented"):
        torch.ops.flashmask.bwd(
            dout,
            q,
            k,
            v,
            out,
            softmax_lse,
            startend,
            block_mask,
            float("nan"),
            True,
            False,
        )
