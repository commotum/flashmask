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
    module_path: str | None = None
    unavailable_reason: str | None = None


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
    ready = bool(ready_fn()) if callable(ready_fn) else False
    return ExtensionStatus(
        loaded=True,
        kernel_ready=ready,
        module_path=getattr(module, "__file__", None),
        unavailable_reason=None
        if ready
        else "compiled extension is present but no compatible sparse FA3 kernel is available for the current device",
    )


def sparse_attention_forward(
    q: Any,
    k: Any,
    v: Any,
    mask: IntervalMask,
    *,
    softmax_scale: float | None = None,
    block_mask: Any | None = None,
    causal: bool | None = None,
) -> tuple[Any, Any]:
    """Call the compiled forward op once the sparse FA3 kernel is available."""

    status = extension_status()
    if not status.loaded or not status.kernel_ready:
        raise NotImplementedError(
            status.unavailable_reason or "FlashMask sparse FA3 backend is not available"
        )
    _validate_interval_mask_call(q, mask, causal)

    import torch

    startend = torch.as_tensor(
        mask.to_list(),
        dtype=torch.int32,
        device=q.device,
    )
    if block_mask is None:
        block_mask = torch.empty(0, dtype=torch.int32, device=q.device)
    elif not hasattr(block_mask, "device"):
        block_mask = torch.as_tensor(block_mask, dtype=torch.int32, device=q.device)

    return torch.ops.flashmask.fwd(
        q,
        k,
        v,
        startend,
        block_mask,
        float("nan") if softmax_scale is None else float(softmax_scale),
        bool(mask.causal if causal is None else causal),
    )


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
