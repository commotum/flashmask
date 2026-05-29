"""Kernel-native attention interface placeholder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._backend import extension_status, sparse_attention_forward
from .core import IntervalMask


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
    module_path: str | None = None
    unavailable_reason: str | None = None


def backend_info(*, backend: str = "fa3") -> BackendInfo:
    """Return backend availability without importing external training stacks.

    The package currently ships the mask ABI and dense reference path only. A
    real backend must report both FA3 compatibility and sparse-mask support; the
    current hard-negative response lets integration tests fail closed instead of
    accidentally treating a dense fallback as the fast path.
    """

    status = extension_status()
    ready = bool(status.loaded and status.kernel_ready)
    return BackendInfo(
        name=str(backend),
        available=ready,
        is_fa3=backend == "fa3",
        supports_sparse_mask=ready,
        supports_backward=False,
        module_path=status.module_path,
        unavailable_reason=None if ready else status.unavailable_reason,
    )


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

    info = backend_info(backend=backend)
    if not info.available:
        raise NotImplementedError(
            info.unavailable_reason
            or f"FlashMask attention backend {backend!r} is not implemented in this package yet"
        )

    output, softmax_lse = sparse_attention_forward(
        q,
        k,
        v,
        mask,
        softmax_scale=softmax_scale,
        block_mask=block_mask,
        causal=causal_override,
    )
    return FlashMaskAttentionResult(output=output, softmax_lse=softmax_lse, backend=backend)


def dense_fallback_is_not_fast_path() -> None:
    """Marker helper for callers looking for an explicit dense fallback.

    Dense reconstruction helpers live in ``flashmask.core`` and are intended for
    tests and parity checks only. Production attention should call
    ``flashmask_attention`` and handle its ``NotImplementedError`` until a real
    backend is installed.
    """

    return None
