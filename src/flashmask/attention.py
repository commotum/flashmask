"""Kernel-native attention interface placeholder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._backend import (
    SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND,
    SPARSE_SM90_FA3_BACKEND_KIND,
    extension_status,
    sparse_attention_forward,
    sparse_attention_forward_with_backward,
)
from .core import IntervalMask


_SPARSE_BACKEND_KIND_BY_BACKEND = {
    "fa3": SPARSE_SM90_FA3_BACKEND_KIND,
    "fa2-compatible": SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND,
}


@dataclass(frozen=True)
class FlashMaskAttentionResult:
    """Return container for future kernel-backed attention calls."""

    output: Any
    softmax_lse: Any
    backend: str


@dataclass(frozen=True)
class BackendInfo:
    """Observed availability of the FlashMask attention backend."""

    name: str
    available: bool
    is_fa3: bool
    supports_sparse_mask: bool
    supports_backward: bool
    is_fa2_compatible: bool = False
    supports_block_mask: bool = False
    supports_native_gqa: bool = False
    supported_dtypes: tuple[str, ...] = ()
    max_head_dim: int | None = None
    training_available: bool = False
    backend_kind: str | None = None
    module_path: str | None = None
    cuda_available: bool | None = None
    compute_capability: tuple[int, int] | None = None
    unavailable_reason: str | None = None


def backend_info(*, backend: str = "fa3") -> BackendInfo:
    """Return backend availability without importing external training stacks.

    The package ships the mask representation, dense reference path, and
    optional backend loader. A real backend must report sparse-mask support for
    the requested architecture; unsupported requests fail closed instead of
    accidentally treating a dense fallback as the fast path.
    """

    status = extension_status()
    requested_kind = _backend_kind_for_name(backend)
    is_fa3 = bool(status.loaded and status.backend_kind == SPARSE_SM90_FA3_BACKEND_KIND)
    is_fa2_compatible = bool(
        status.loaded and status.backend_kind == SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND
    )
    ready = bool(
        requested_kind is not None
        and status.backend_kind == requested_kind
        and status.loaded
        and status.kernel_ready
        and status.forward_ready
    )
    known_sparse_backend = bool(is_fa3 or is_fa2_compatible)
    supports_backward = bool(ready and status.backward_ready)
    return BackendInfo(
        name=str(backend),
        available=ready,
        is_fa3=is_fa3,
        supports_sparse_mask=ready,
        supports_backward=supports_backward,
        is_fa2_compatible=is_fa2_compatible,
        supports_block_mask=False,
        supports_native_gqa=False,
        supported_dtypes=("float16", "bfloat16") if known_sparse_backend else (),
        max_head_dim=128 if known_sparse_backend else None,
        training_available=supports_backward,
        backend_kind=status.backend_kind,
        module_path=status.module_path,
        cuda_available=status.cuda_available,
        compute_capability=status.compute_capability,
        unavailable_reason=_backend_unavailable_reason(
            backend=backend,
            requested_kind=requested_kind,
            status=status,
            ready=ready,
        ),
    )


def _backend_kind_for_name(backend: str) -> str | None:
    return _SPARSE_BACKEND_KIND_BY_BACKEND.get(backend)


def _backend_unavailable_reason(
    *,
    backend: str,
    requested_kind: str | None,
    status: Any,
    ready: bool,
) -> str | None:
    if ready:
        return None
    if requested_kind is None:
        return f"FlashMask attention backend {backend!r} is unknown"
    if status.backend_kind and status.backend_kind != requested_kind:
        return (
            f"loaded backend kind {status.backend_kind!r} does not match "
            f"requested backend {backend!r}"
        )
    return status.unavailable_reason


def verify_backend(
    *,
    backend: str = "fa3",
    require_fa3: bool = True,
    require_sparse: bool = True,
    require_backward: bool = False,
) -> BackendInfo:
    """Verify that a production FlashMask backend is available.

    Raises ``RuntimeError`` until the CUDA/FA3 sparse attention extension is
    installed. This is intentionally separate from dense reference utilities.
    """

    info = backend_info(backend=backend)
    if not info.available:
        raise RuntimeError(info.unavailable_reason or f"backend {backend!r} is unavailable")
    if require_fa3 and not info.is_fa3:
        raise RuntimeError(f"backend {backend!r} is not FA3-compatible")
    if require_sparse and not info.supports_sparse_mask:
        raise RuntimeError(f"backend {backend!r} does not support sparse FlashMask metadata")
    if require_backward and not info.supports_backward:
        raise RuntimeError(f"backend {backend!r} does not support backward")
    return info


def flashmask_attention(
    q: Any,
    k: Any,
    v: Any,
    mask: IntervalMask,
    *,
    backend: str = "fa3",
    softmax_scale: float | None = None,
    block_mask: Any | None = None,
    causal: bool | None = None,
    is_causal: bool | None = None,
) -> FlashMaskAttentionResult:
    """Run FlashMask attention through a kernel-native backend.

    The function is intentionally fail-closed instead of using a dense fallback,
    so callers cannot mistake correctness scaffolding for the real
    FlashAttention 3 fast path.
    """

    if causal is not None and is_causal is not None and bool(causal) != bool(is_causal):
        raise ValueError("causal and is_causal disagree")
    causal_override = causal if causal is not None else is_causal

    if backend not in _SPARSE_BACKEND_KIND_BY_BACKEND:
        supported = ", ".join(repr(name) for name in _SPARSE_BACKEND_KIND_BY_BACKEND)
        raise ValueError(
            f"FlashMask attention backend must be one of {supported}, got {backend!r}"
        )
    requested_kind = _backend_kind_for_name(backend)
    _validate_experimental_forward_limits(q, k, v, block_mask)
    needs_backward = any(
        bool(getattr(tensor, "requires_grad", False))
        for tensor in (q, k, v)
    )
    try:
        verify_backend(
            backend=backend,
            require_fa3=backend == "fa3",
            require_sparse=True,
            require_backward=needs_backward,
        )
    except RuntimeError as exc:
        raise NotImplementedError(
            str(exc) or f"FlashMask attention backend {backend!r} is not implemented"
        ) from exc

    forward = sparse_attention_forward_with_backward if needs_backward else sparse_attention_forward
    output, softmax_lse = forward(
        q,
        k,
        v,
        mask,
        softmax_scale=softmax_scale,
        block_mask=block_mask,
        causal=causal_override,
        backend_kind=requested_kind,
    )
    return FlashMaskAttentionResult(output=output, softmax_lse=softmax_lse, backend=backend)


def _validate_experimental_forward_limits(q: Any, k: Any, v: Any, block_mask: Any | None) -> None:
    block_mask_numel = getattr(block_mask, "numel", None)
    if block_mask is not None:
        if not callable(block_mask_numel) or int(block_mask_numel()) != 0:
            raise NotImplementedError("FlashMask experimental forward does not support block_mask yet")

    q_shape = _shape_tuple(q)
    k_shape = _shape_tuple(k)
    v_shape = _shape_tuple(v)
    if q_shape is None or k_shape is None or v_shape is None:
        return
    if len(q_shape) != 4 or len(k_shape) != 4 or len(v_shape) != 4:
        return

    if q_shape[2] != k_shape[2] or k_shape[2] != v_shape[2]:
        raise NotImplementedError("FlashMask experimental forward does not support native GQA yet")
    if q_shape[3] > 128:
        raise NotImplementedError("FlashMask experimental forward supports head_dim <= 128 only")
    if v_shape[3] > 128:
        raise NotImplementedError("FlashMask experimental forward supports value head_dim <= 128 only")


def _shape_tuple(tensor: Any) -> tuple[int, ...] | None:
    shape = getattr(tensor, "shape", None)
    if shape is not None:
        return tuple(int(dim) for dim in shape)
    size = getattr(tensor, "size", None)
    if callable(size):
        try:
            return tuple(int(dim) for dim in size())
        except TypeError:
            return None
    return None


def dense_fallback_is_not_fast_path() -> None:
    """Marker helper for callers looking for an explicit dense fallback.

    Dense reconstruction helpers live in ``flashmask.core`` and are intended for
    tests and parity checks only. Production attention should call
    ``flashmask_attention`` and handle its ``NotImplementedError`` until a real
    backend is installed.
    """

    return None
