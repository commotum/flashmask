"""Lazy access to the optional CUDA backend."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any

from .core import IntervalMask


@dataclass(frozen=True)
class ExtensionStatus:
    """Load state for the optional compiled extension."""

    loaded: bool
    kernel_ready: bool
    forward_ready: bool = False
    backward_ready: bool = False
    backend_kind: str | None = None
    module_path: str | None = None
    cuda_available: bool | None = None
    compute_capability: tuple[int, int] | None = None
    supported_compute_capabilities: tuple[tuple[int, int], ...] = ()
    unavailable_reason: str | None = None


SPARSE_SM90_FA3_BACKEND_KIND = "sm90_sparse_fa3"
SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND = "sm8x_sparse_fa2_compatible"
SPARSE_FA3_BACKEND_KIND = SPARSE_SM90_FA3_BACKEND_KIND

SUPPORTED_SPARSE_BACKEND_KINDS = frozenset(
    {
        SPARSE_SM90_FA3_BACKEND_KIND,
        SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND,
    }
)
REPRESENTABLE_SPARSE_BACKEND_KINDS = frozenset(
    {
        SPARSE_SM90_FA3_BACKEND_KIND,
        SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND,
    }
)

_EMPTY_BLOCK_MASK_CACHE: dict[tuple[int, int, str], Any] = {}


def extension_status() -> ExtensionStatus:
    """Return compiled-extension status without importing torch eagerly."""

    try:
        module = import_module("flashmask._C")
    except Exception as exc:
        reason = str(exc)
        if "flashmask._C" in reason:
            reason = "FlashMask sparse FA3 kernels are not implemented"
        return ExtensionStatus(
            loaded=False,
            kernel_ready=False,
            unavailable_reason=reason,
        )

    ready_fn = getattr(module, "kernel_ready", None)
    forward_ready_fn = getattr(module, "forward_ready", None)
    backward_ready_fn = getattr(module, "backward_ready", None)
    kind_fn = getattr(module, "backend_kind", None)
    cuda_available_fn = getattr(module, "cuda_available", None)
    compute_capability_fn = getattr(module, "current_compute_capability", None)
    supported_compute_capabilities_fn = getattr(module, "supported_compute_capabilities", None)
    reported_ready = bool(ready_fn()) if callable(ready_fn) else False
    reported_forward_ready = (
        bool(forward_ready_fn()) if callable(forward_ready_fn) else reported_ready
    )
    reported_backward_ready = bool(backward_ready_fn()) if callable(backward_ready_fn) else False
    backend_kind = str(kind_fn()) if callable(kind_fn) else None
    cuda_available = bool(cuda_available_fn()) if callable(cuda_available_fn) else None
    compute_capability = _normalize_compute_capability(
        compute_capability_fn() if callable(compute_capability_fn) else None
    )
    supported_compute_capabilities = _normalize_supported_compute_capabilities(
        supported_compute_capabilities_fn() if callable(supported_compute_capabilities_fn) else None
    )
    supported_backend_kind = backend_kind in SUPPORTED_SPARSE_BACKEND_KINDS
    ready = bool(reported_ready and supported_backend_kind)
    forward_ready = bool(reported_forward_ready and supported_backend_kind)
    backward_ready = bool(reported_backward_ready and supported_backend_kind)
    unavailable_reason = None
    if backend_kind not in REPRESENTABLE_SPARSE_BACKEND_KINDS:
        unavailable_reason = f"compiled extension backend kind {backend_kind!r} is not supported"
    elif not reported_ready or not reported_forward_ready:
        unavailable_reason = (
            "compiled extension is present but no compatible sparse kernel "
            "is available for the current device"
        )
    return ExtensionStatus(
        loaded=True,
        kernel_ready=ready,
        forward_ready=forward_ready,
        backward_ready=backward_ready,
        backend_kind=backend_kind,
        module_path=getattr(module, "__file__", None),
        cuda_available=cuda_available,
        compute_capability=compute_capability,
        supported_compute_capabilities=supported_compute_capabilities,
        unavailable_reason=unavailable_reason,
    )


def _normalize_compute_capability(value: Any) -> tuple[int, int] | None:
    if value is None:
        return None
    if hasattr(value, "tolist"):
        value = value.tolist()
    try:
        major, minor = value
    except (TypeError, ValueError):
        return None
    return int(major), int(minor)


def _normalize_supported_compute_capabilities(value: Any) -> tuple[tuple[int, int], ...]:
    if value is None:
        return ()
    if hasattr(value, "tolist"):
        value = value.tolist()
    try:
        items = tuple(value)
    except TypeError:
        return ()
    normalized: list[tuple[int, int]] = []
    for item in items:
        capability = _normalize_compute_capability(item)
        if capability is not None:
            normalized.append(capability)
    return tuple(normalized)


def sparse_attention_forward(
    q: Any,
    k: Any,
    v: Any,
    mask: IntervalMask,
    *,
    softmax_scale: float | None = None,
    block_mask: Any | None = None,
    causal: bool | None = None,
    backend_kind: str = SPARSE_SM90_FA3_BACKEND_KIND,
) -> tuple[Any, Any]:
    """Call the compiled forward op once the requested sparse kernel is available."""

    torch, startend, block_mask, softmax_scale, causal = _prepare_sparse_attention_call(
        q,
        mask,
        softmax_scale=softmax_scale,
        block_mask=block_mask,
        causal=causal,
        backend_kind=backend_kind,
        require_backward=False,
    )

    return torch.ops.flashmask.fwd(
        q,
        k,
        v,
        startend,
        block_mask,
        softmax_scale,
        causal,
    )


def sparse_attention_forward_with_backward(
    q: Any,
    k: Any,
    v: Any,
    mask: IntervalMask,
    *,
    softmax_scale: float | None = None,
    block_mask: Any | None = None,
    causal: bool | None = None,
    backend_kind: str = SPARSE_SM90_FA3_BACKEND_KIND,
    deterministic: bool = False,
) -> tuple[Any, Any]:
    """Call forward through an autograd wrapper backed by ``flashmask::bwd``."""

    torch, startend, block_mask, softmax_scale, causal = _prepare_sparse_attention_call(
        q,
        mask,
        softmax_scale=softmax_scale,
        block_mask=block_mask,
        causal=causal,
        backend_kind=backend_kind,
        require_backward=True,
    )
    function = _flashmask_attention_autograd_function(torch)
    return function.apply(
        q,
        k,
        v,
        startend,
        block_mask,
        softmax_scale,
        causal,
        bool(deterministic),
    )


def _startend_tensor_for_device(torch: Any, mask: IntervalMask, device: Any) -> Any:
    cache_key = str(device)
    cache = getattr(mask, "_torch_startend_cache", None)
    if cache is None:
        cache = {}
        object.__setattr__(mask, "_torch_startend_cache", cache)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    startend = torch.as_tensor(
        mask.to_list(),
        dtype=torch.int32,
        device=device,
    )
    cache[cache_key] = startend
    return startend


def _empty_block_mask_for_device(torch: Any, device: Any) -> Any:
    cache_key = (id(torch), id(getattr(torch, "empty", None)), str(device))
    cached = _EMPTY_BLOCK_MASK_CACHE.get(cache_key)
    if cached is not None:
        return cached
    block_mask = torch.empty(0, dtype=torch.int32, device=device)
    _EMPTY_BLOCK_MASK_CACHE[cache_key] = block_mask
    return block_mask


def _prepare_sparse_attention_call(
    q: Any,
    mask: IntervalMask,
    *,
    softmax_scale: float | None,
    block_mask: Any | None,
    causal: bool | None,
    backend_kind: str,
    require_backward: bool,
) -> tuple[Any, Any, Any, float, bool]:
    status = extension_status()
    if (
        not status.loaded
        or not status.kernel_ready
        or not status.forward_ready
        or status.backend_kind != backend_kind
    ):
        raise NotImplementedError(
            status.unavailable_reason or f"FlashMask sparse backend {backend_kind!r} is not available"
        )
    if require_backward and not status.backward_ready:
        raise NotImplementedError(f"FlashMask sparse backend {backend_kind!r} does not support backward")
    _validate_interval_mask_call(q, mask, causal)

    import torch

    startend = _startend_tensor_for_device(torch, mask, q.device)
    if block_mask is None:
        block_mask = _empty_block_mask_for_device(torch, q.device)
    elif not hasattr(block_mask, "device"):
        block_mask = torch.as_tensor(block_mask, dtype=torch.int32, device=q.device)

    scale = float("nan") if softmax_scale is None else float(softmax_scale)
    return torch, startend, block_mask, scale, bool(mask.causal if causal is None else causal)


def _flashmask_attention_autograd_function(torch: Any) -> Any:
    class FlashMaskAttentionAutograd(torch.autograd.Function):  # type: ignore[name-defined]
        @staticmethod
        def forward(
            ctx: Any,
            q: Any,
            k: Any,
            v: Any,
            startend: Any,
            block_mask: Any,
            softmax_scale: float,
            causal: bool,
            deterministic: bool,
        ) -> tuple[Any, Any]:
            out, softmax_lse = torch.ops.flashmask.fwd(
                q,
                k,
                v,
                startend,
                block_mask,
                softmax_scale,
                causal,
            )
            if hasattr(ctx, "set_materialize_grads"):
                ctx.set_materialize_grads(False)
            ctx.save_for_backward(q, k, v, out, softmax_lse, startend, block_mask)
            ctx.softmax_scale = float(softmax_scale)
            ctx.causal = bool(causal)
            ctx.deterministic = bool(deterministic)
            return out, softmax_lse

        @staticmethod
        def backward(ctx: Any, dout: Any, dsoftmax_lse: Any = None) -> tuple[Any, ...]:
            if dsoftmax_lse is not None:
                raise RuntimeError("FlashMask autograd does not support gradients through softmax_lse")
            q, k, v, out, softmax_lse, startend, block_mask = ctx.saved_tensors
            dq, dk, dv = torch.ops.flashmask.bwd(
                dout,
                q,
                k,
                v,
                out,
                softmax_lse,
                startend,
                block_mask,
                ctx.softmax_scale,
                ctx.causal,
                ctx.deterministic,
            )
            return dq, dk, dv, None, None, None, None, None

    return FlashMaskAttentionAutograd


def _validate_interval_mask_call(q: Any, mask: IntervalMask, causal: bool | None) -> None:
    if causal is not None and bool(causal) != mask.causal:
        raise ValueError("causal override does not match IntervalMask.causal")

    shape = getattr(q, "shape", None)
    if shape is None:
        size = getattr(q, "size", None)
        if callable(size):
            query_len = int(size(1))
        else:
            raise TypeError("q must expose shape or size for seqlen_q validation")
    else:
        query_len = int(shape[1])

    if mask.seqlen_q is not None and mask.seqlen_q != query_len:
        raise ValueError(
            f"IntervalMask.seqlen_q={mask.seqlen_q} does not match q sequence length {query_len}"
        )
    if mask.max_bound > query_len:
        raise ValueError(
            f"IntervalMask max bound {mask.max_bound} exceeds q sequence length {query_len}"
        )
    _validate_interval_order(mask)


def _validate_interval_order(mask: IntervalMask) -> None:
    cache_key = (mask.causal, mask.shape)
    if getattr(mask, "_interval_order_validated", None) == cache_key:
        return

    if mask.causal and mask.bound_num not in (1, 2):
        raise ValueError("causal IntervalMask requires bound_num 1 or 2")
    if not mask.causal and mask.bound_num not in (2, 4):
        raise ValueError("non-causal IntervalMask requires bound_num 2 or 4")

    for b_idx, batch in enumerate(mask.startend_row_indices):
        for h_idx, head in enumerate(batch):
            for k_idx, bounds in enumerate(head):
                if mask.causal:
                    if mask.bound_num == 2 and bounds[1] < bounds[0]:
                        _raise_bad_interval_order(b_idx, h_idx, k_idx, bounds)
                elif mask.bound_num == 2:
                    if bounds[0] < bounds[1]:
                        _raise_bad_interval_order(b_idx, h_idx, k_idx, bounds)
                elif bounds[1] < bounds[0] or bounds[3] < bounds[2]:
                    _raise_bad_interval_order(b_idx, h_idx, k_idx, bounds)
    object.__setattr__(mask, "_interval_order_validated", cache_key)


def _raise_bad_interval_order(
    b_idx: int,
    h_idx: int,
    k_idx: int,
    bounds: tuple[int, ...],
) -> None:
    raise ValueError(
        "IntervalMask bounds are not ordered at "
        f"batch={b_idx}, head={h_idx}, key={k_idx}: {bounds}"
    )
