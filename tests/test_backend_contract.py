from __future__ import annotations

import sys
import types
from dataclasses import dataclass

import pytest

import flashmask
import flashmask._backend as backend_module
import flashmask.attention as attention_module
from flashmask._backend import (
    ExtensionStatus,
    SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND,
    SPARSE_SM90_FA3_BACKEND_KIND,
    sparse_attention_forward,
    sparse_attention_forward_with_backward,
)


@dataclass(frozen=True)
class FakeTensor:
    shape: tuple[int, ...]
    device: str = "cuda"
    requires_grad: bool = False


@dataclass(frozen=True)
class FakeBlockMask:
    elements: int

    def numel(self) -> int:
        return self.elements


def _force_ready_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        backend_module,
        "extension_status",
        lambda: ExtensionStatus(
            loaded=True,
            kernel_ready=True,
            forward_ready=True,
            backend_kind=SPARSE_SM90_FA3_BACKEND_KIND,
        ),
    )


def test_sparse_forward_accepts_sm8x_fa2_compatible_kind(monkeypatch):
    monkeypatch.setattr(
        backend_module,
        "extension_status",
        lambda: ExtensionStatus(
            loaded=True,
            kernel_ready=True,
            forward_ready=True,
            backend_kind=SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND,
        ),
    )
    mask = flashmask.IntervalMask([[[[1]]]], causal=True, seqlen_q=1)
    q = FakeTensor((1, 1, 1, 1))
    startend_tensor = object()
    block_tensor = object()
    out_tensor = object()
    lse_tensor = object()
    calls = []

    fake_torch = types.ModuleType("torch")
    fake_torch.int32 = "int32"

    def as_tensor(value, *, dtype, device):
        calls.append(("as_tensor", value, dtype, device))
        return startend_tensor

    def empty(*shape, dtype, device):
        calls.append(("empty", shape, dtype, device))
        return block_tensor

    class FakeFlashMaskOps:
        def fwd(self, *args):
            calls.append(("fwd", args))
            return out_tensor, lse_tensor

    fake_torch.as_tensor = as_tensor
    fake_torch.empty = empty
    fake_torch.ops = types.SimpleNamespace(flashmask=FakeFlashMaskOps())
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    assert sparse_attention_forward(
        q,
        q,
        q,
        mask,
        backend_kind=SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND,
    ) == (out_tensor, lse_tensor)
    assert [call[0] for call in calls] == ["as_tensor", "empty", "fwd"]


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


def test_flashmask_attention_calls_sparse_torch_op_once(monkeypatch):
    mask = flashmask.IntervalMask([[[[1]]]], causal=True, seqlen_q=1)
    q = FakeTensor((1, 1, 1, 1))
    k = object()
    v = object()
    startend_tensor = object()
    block_tensor = object()
    out_tensor = object()
    lse_tensor = object()
    calls = []

    fake_torch = types.ModuleType("torch")
    fake_torch.int32 = "int32"

    def as_tensor(value, *, dtype, device):
        calls.append(("as_tensor", value, dtype, device))
        return startend_tensor

    def empty(*shape, dtype, device):
        calls.append(("empty", shape, dtype, device))
        return block_tensor

    class FakeFlashMaskOps:
        def fwd(self, *args):
            calls.append(("fwd", args))
            return out_tensor, lse_tensor

    fake_torch.as_tensor = as_tensor
    fake_torch.empty = empty
    fake_torch.ops = types.SimpleNamespace(flashmask=FakeFlashMaskOps())

    ready = lambda: ExtensionStatus(
        loaded=True,
        kernel_ready=True,
        forward_ready=True,
        backend_kind="sm90_sparse_fa3",
    )
    monkeypatch.setattr(attention_module, "extension_status", ready)
    monkeypatch.setattr(backend_module, "extension_status", ready)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    result = flashmask.flashmask_attention(q, k, v, mask, softmax_scale=0.5)

    assert result.output is out_tensor
    assert result.softmax_lse is lse_tensor
    assert result.backend == "fa3"
    fwd_calls = [call for call in calls if call[0] == "fwd"]
    assert fwd_calls == [
        (
            "fwd",
            (q, k, v, startend_tensor, block_tensor, 0.5, True),
        )
    ]


def test_sparse_forward_caches_startend_tensor_per_device(monkeypatch):
    mask = flashmask.IntervalMask([[[[1]]]], causal=True, seqlen_q=1)
    q = FakeTensor((1, 1, 1, 1), device="cuda:0")
    startend_tensor = object()
    block_tensor = object()
    calls = []

    fake_torch = types.ModuleType("torch")
    fake_torch.int32 = "int32"

    def as_tensor(value, *, dtype, device):
        calls.append(("as_tensor", value, dtype, device))
        return startend_tensor

    def empty(*shape, dtype, device):
        calls.append(("empty", shape, dtype, device))
        return block_tensor

    class FakeFlashMaskOps:
        def fwd(self, *args):
            calls.append(("fwd", args))
            return object(), object()

    fake_torch.as_tensor = as_tensor
    fake_torch.empty = empty
    fake_torch.ops = types.SimpleNamespace(flashmask=FakeFlashMaskOps())

    ready = lambda: ExtensionStatus(
        loaded=True,
        kernel_ready=True,
        forward_ready=True,
        backend_kind=SPARSE_SM90_FA3_BACKEND_KIND,
    )
    monkeypatch.setattr(backend_module, "extension_status", ready)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    sparse_attention_forward(q, q, q, mask)
    sparse_attention_forward(q, q, q, mask)

    as_tensor_calls = [call for call in calls if call[0] == "as_tensor"]
    assert as_tensor_calls == [("as_tensor", mask.to_list(), "int32", "cuda:0")]
    assert [call[0] for call in calls].count("fwd") == 2


@pytest.mark.parametrize(
    ("backend", "backend_kind"),
    [
        ("fa3", SPARSE_SM90_FA3_BACKEND_KIND),
        ("fa2-compatible", SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND),
    ],
)
def test_flashmask_attention_requires_backward_for_grad_inputs_before_dispatch(
    monkeypatch,
    backend,
    backend_kind,
):
    mask = flashmask.IntervalMask([[[[1]]]], causal=True, seqlen_q=1)
    q = FakeTensor((1, 1, 1, 1), requires_grad=True)
    calls = []

    ready = lambda: ExtensionStatus(
        loaded=True,
        kernel_ready=True,
        forward_ready=True,
        backward_ready=False,
        backend_kind=backend_kind,
    )
    monkeypatch.setattr(attention_module, "extension_status", ready)
    monkeypatch.setattr(backend_module, "extension_status", ready)

    fake_torch = types.ModuleType("torch")
    fake_torch.int32 = "int32"

    def fail_raw_call(*args, **kwargs):
        calls.append(("raw", args, kwargs))
        raise AssertionError("raw op should not be called without backward support")

    fake_torch.as_tensor = fail_raw_call
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    with pytest.raises(NotImplementedError, match="does not support backward"):
        flashmask.flashmask_attention(q, object(), object(), mask, backend=backend)

    assert calls == []


def test_sparse_attention_forward_with_backward_routes_autograd_to_bwd(monkeypatch):
    mask = flashmask.IntervalMask([[[[1]]]], causal=True, seqlen_q=1)
    q = FakeTensor((1, 1, 1, 1), requires_grad=True)
    k = FakeTensor((1, 1, 1, 1), requires_grad=True)
    v = FakeTensor((1, 1, 1, 1), requires_grad=True)
    startend_tensor = object()
    block_tensor = object()
    out_tensor = object()
    lse_tensor = object()
    dout_tensor = object()
    dq_tensor = object()
    dk_tensor = object()
    dv_tensor = object()
    calls = []

    ready = lambda: ExtensionStatus(
        loaded=True,
        kernel_ready=True,
        forward_ready=True,
        backward_ready=True,
        backend_kind=SPARSE_SM90_FA3_BACKEND_KIND,
    )
    monkeypatch.setattr(backend_module, "extension_status", ready)

    fake_torch = types.ModuleType("torch")
    fake_torch.int32 = "int32"

    def as_tensor(value, *, dtype, device):
        calls.append(("as_tensor", value, dtype, device))
        return startend_tensor

    def empty(*shape, dtype, device):
        calls.append(("empty", shape, dtype, device))
        return block_tensor

    class FakeFunction:
        last_cls = None
        last_ctx = None

        @classmethod
        def apply(cls, *args):
            ctx = types.SimpleNamespace()
            ctx.save_for_backward = lambda *tensors: setattr(ctx, "saved_tensors", tensors)
            FakeFunction.last_cls = cls
            FakeFunction.last_ctx = ctx
            return cls.forward(ctx, *args)

    class FakeFlashMaskOps:
        def fwd(self, *args):
            calls.append(("fwd", args))
            return out_tensor, lse_tensor

        def bwd(self, *args):
            calls.append(("bwd", args))
            return dq_tensor, dk_tensor, dv_tensor

    fake_torch.as_tensor = as_tensor
    fake_torch.empty = empty
    fake_torch.autograd = types.SimpleNamespace(Function=FakeFunction)
    fake_torch.ops = types.SimpleNamespace(flashmask=FakeFlashMaskOps())
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    output, lse = sparse_attention_forward_with_backward(
        q,
        k,
        v,
        mask,
        softmax_scale=0.25,
    )
    grads = FakeFunction.last_cls.backward(FakeFunction.last_ctx, dout_tensor, None)

    assert output is out_tensor
    assert lse is lse_tensor
    assert ("fwd", (q, k, v, startend_tensor, block_tensor, 0.25, True)) in calls
    assert (
        "bwd",
        (
            dout_tensor,
            q,
            k,
            v,
            out_tensor,
            lse_tensor,
            startend_tensor,
            block_tensor,
            0.25,
            True,
            False,
        ),
    ) in calls
    assert grads == (dq_tensor, dk_tensor, dv_tensor, None, None, None, None, None)


def test_flashmask_attention_uses_backward_surface_for_grad_inputs(monkeypatch):
    mask = flashmask.IntervalMask([[[[1]]]], causal=True, seqlen_q=1)
    q = FakeTensor((1, 1, 1, 1), requires_grad=True)
    calls = []

    ready = lambda: ExtensionStatus(
        loaded=True,
        kernel_ready=True,
        forward_ready=True,
        backward_ready=True,
        backend_kind=SPARSE_SM90_FA3_BACKEND_KIND,
    )
    monkeypatch.setattr(attention_module, "extension_status", ready)
    monkeypatch.setattr(backend_module, "extension_status", ready)

    def fail_forward(*args, **kwargs):
        raise AssertionError("grad-tracked FlashMask calls must use the backward surface")

    def backward_forward(*args, **kwargs):
        calls.append((args, kwargs))
        return "out", "lse"

    monkeypatch.setattr(attention_module, "sparse_attention_forward", fail_forward)
    monkeypatch.setattr(
        attention_module,
        "sparse_attention_forward_with_backward",
        backward_forward,
    )

    result = flashmask.flashmask_attention(q, q, q, mask, softmax_scale=0.5)

    assert result.output == "out"
    assert result.softmax_lse == "lse"
    assert calls == [
        (
            (q, q, q, mask),
            {
                "softmax_scale": 0.5,
                "block_mask": None,
                "causal": None,
                "backend_kind": SPARSE_SM90_FA3_BACKEND_KIND,
            },
        )
    ]


def test_flashmask_attention_rejects_nonempty_block_mask_before_dispatch(monkeypatch):
    mask = flashmask.IntervalMask([[[[1]]]], causal=True, seqlen_q=1)
    q = FakeTensor((1, 1, 1, 128))

    ready = lambda: ExtensionStatus(
        loaded=True,
        kernel_ready=True,
        forward_ready=True,
        backend_kind="sm90_sparse_fa3",
    )
    monkeypatch.setattr(attention_module, "extension_status", ready)

    with pytest.raises(NotImplementedError, match="block_mask"):
        flashmask.flashmask_attention(
            q,
            q,
            q,
            mask,
            block_mask=FakeBlockMask(1),
        )


def test_flashmask_attention_rejects_nontensor_block_mask_before_dispatch(monkeypatch):
    mask = flashmask.IntervalMask([[[[1]]]], causal=True, seqlen_q=1)
    q = FakeTensor((1, 1, 1, 128))

    ready = lambda: ExtensionStatus(
        loaded=True,
        kernel_ready=True,
        forward_ready=True,
        backend_kind="sm90_sparse_fa3",
    )
    monkeypatch.setattr(attention_module, "extension_status", ready)

    with pytest.raises(NotImplementedError, match="block_mask"):
        flashmask.flashmask_attention(q, q, q, mask, block_mask=[0])


def test_flashmask_attention_rejects_native_gqa_before_dispatch(monkeypatch):
    mask = flashmask.IntervalMask([[[[1]]]], causal=True, seqlen_q=1)

    ready = lambda: ExtensionStatus(
        loaded=True,
        kernel_ready=True,
        forward_ready=True,
        backend_kind="sm90_sparse_fa3",
    )
    monkeypatch.setattr(attention_module, "extension_status", ready)

    with pytest.raises(NotImplementedError, match="native GQA"):
        flashmask.flashmask_attention(
            FakeTensor((1, 1, 4, 128)),
            FakeTensor((1, 1, 2, 128)),
            FakeTensor((1, 1, 2, 128)),
            mask,
        )


def test_flashmask_attention_rejects_unsupported_head_dim_before_dispatch(monkeypatch):
    mask = flashmask.IntervalMask([[[[1]]]], causal=True, seqlen_q=1)

    ready = lambda: ExtensionStatus(
        loaded=True,
        kernel_ready=True,
        forward_ready=True,
        backend_kind="sm90_sparse_fa3",
    )
    monkeypatch.setattr(attention_module, "extension_status", ready)

    with pytest.raises(NotImplementedError, match="head_dim <= 128"):
        flashmask.flashmask_attention(
            FakeTensor((1, 1, 1, 192)),
            FakeTensor((1, 1, 1, 192)),
            FakeTensor((1, 1, 1, 192)),
            mask,
        )


def test_flashmask_attention_rejects_unsupported_value_head_dim_before_dispatch(monkeypatch):
    mask = flashmask.IntervalMask([[[[1]]]], causal=True, seqlen_q=1)

    ready = lambda: ExtensionStatus(
        loaded=True,
        kernel_ready=True,
        forward_ready=True,
        backend_kind="sm90_sparse_fa3",
    )
    monkeypatch.setattr(attention_module, "extension_status", ready)

    with pytest.raises(NotImplementedError, match="value head_dim <= 128"):
        flashmask.flashmask_attention(
            FakeTensor((1, 1, 1, 128)),
            FakeTensor((1, 1, 1, 128)),
            FakeTensor((1, 1, 1, 192)),
            mask,
        )


def test_flashmask_attention_allows_non_128_head_dim_to_dispatch(monkeypatch):
    mask = flashmask.IntervalMask([[[[1]]]], causal=True, seqlen_q=1)
    q = FakeTensor((1, 1, 1, 64))
    startend_tensor = object()
    block_tensor = object()
    out_tensor = object()
    lse_tensor = object()
    calls = []

    ready = lambda: ExtensionStatus(
        loaded=True,
        kernel_ready=True,
        forward_ready=True,
        backend_kind="sm90_sparse_fa3",
    )
    monkeypatch.setattr(attention_module, "extension_status", ready)
    monkeypatch.setattr(backend_module, "extension_status", ready)

    fake_torch = types.ModuleType("torch")
    fake_torch.int32 = "int32"

    def as_tensor(value, *, dtype, device):
        return startend_tensor

    def empty(*shape, dtype, device):
        return block_tensor

    class FakeFlashMaskOps:
        def fwd(self, *args):
            calls.append(args)
            return out_tensor, lse_tensor

    fake_torch.as_tensor = as_tensor
    fake_torch.empty = empty
    fake_torch.ops = types.SimpleNamespace(flashmask=FakeFlashMaskOps())
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    result = flashmask.flashmask_attention(q, q, q, mask)

    assert result.output is out_tensor
    assert len(calls) == 1
    assert calls[0][:5] == (q, q, q, startend_tensor, block_tensor)
    assert calls[0][5] != calls[0][5]
    assert calls[0][6] is True


def test_backend_info_exposes_forward_only_capability_limits(monkeypatch):
    monkeypatch.setattr(
        attention_module,
        "extension_status",
        lambda: ExtensionStatus(
            loaded=True,
            kernel_ready=True,
            forward_ready=True,
            backward_ready=False,
            backend_kind="sm90_sparse_fa3",
            cuda_available=True,
            compute_capability=(9, 0),
        ),
    )

    info = flashmask.backend_info()

    assert info.available is True
    assert info.supports_backward is False
    assert info.training_available is False
    assert info.supports_block_mask is False
    assert info.supports_native_gqa is False
    assert info.supported_dtypes == ("float16", "bfloat16")
    assert info.max_head_dim == 128
    assert info.cuda_available is True
    assert info.compute_capability == (9, 0)


def test_backend_info_supports_sm8x_fa2_compatible_kind(monkeypatch):
    monkeypatch.setattr(
        attention_module,
        "extension_status",
        lambda: ExtensionStatus(
            loaded=True,
            kernel_ready=True,
            forward_ready=True,
            backend_kind=SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND,
            cuda_available=True,
            compute_capability=(8, 6),
        ),
    )
    mask = flashmask.IntervalMask([[[[1]]]], causal=True, seqlen_q=1)
    q = FakeTensor((1, 1, 1, 64))

    info = flashmask.backend_info(backend="fa2-compatible")

    assert info.available is True
    assert info.is_fa3 is False
    assert info.is_fa2_compatible is True
    assert info.supports_sparse_mask is True
    assert info.supports_backward is False
    assert info.training_available is False
    assert info.supported_dtypes == ("float16", "bfloat16")
    assert info.max_head_dim == 128
    assert info.backend_kind == SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND
    assert info.cuda_available is True
    assert info.compute_capability == (8, 6)
    assert info.unavailable_reason is None
    assert flashmask.verify_backend(backend="fa2-compatible", require_fa3=False) == info

    calls = []
    monkeypatch.setattr(
        attention_module,
        "sparse_attention_forward",
        lambda *args, **kwargs: calls.append((args, kwargs)) or ("out", "lse"),
    )
    result = flashmask.flashmask_attention(q, q, q, mask, backend="fa2-compatible")
    assert result.output == "out"
    assert result.softmax_lse == "lse"
    assert result.backend == "fa2-compatible"
    assert calls[0][1]["backend_kind"] == SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND


def test_flashmask_attention_rejects_unknown_backend_before_dispatch(monkeypatch):
    ready = lambda: ExtensionStatus(
        loaded=True,
        kernel_ready=True,
        forward_ready=True,
        backend_kind="sm90_sparse_fa3",
    )
    monkeypatch.setattr(attention_module, "extension_status", ready)
    mask = flashmask.IntervalMask([[[[1]]]], causal=True, seqlen_q=1)

    with pytest.raises(ValueError, match="must be one of"):
        flashmask.flashmask_attention(None, None, None, mask, backend="sdpa")


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
