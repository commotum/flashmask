"""Kernel-native attention interface placeholder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._backend import (
    SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND,
    SPARSE_SM90_FA3_BACKEND_KIND,
    _build_hint_for_backend_kind,
    extension_status,
    sparse_attention_forward,
    sparse_attention_forward_with_backward,
)
from .core import IntervalMask


_SPARSE_BACKEND_KIND_BY_BACKEND = {
    "fa3": SPARSE_SM90_FA3_BACKEND_KIND,
    "sm90-fa3": SPARSE_SM90_FA3_BACKEND_KIND,
    "fa2-compatible": SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND,
    "sm8x-fa2-compatible": SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND,
}
_CANONICAL_BACKEND_BY_ALIAS = {
    "auto": "auto",
    "fa3": "fa3",
    "sm90-fa3": "fa3",
    "fa2-compatible": "fa2-compatible",
    "sm8x-fa2-compatible": "fa2-compatible",
}
_BACKEND_BY_SPARSE_BACKEND_KIND = {
    SPARSE_SM90_FA3_BACKEND_KIND: "fa3",
    SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND: "fa2-compatible",
}
_PROVEN_COMPUTE_CAPABILITIES_BY_BACKEND_KIND = {
    SPARSE_SM90_FA3_BACKEND_KIND: frozenset(),
    SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND: frozenset({(8, 6)}),
}


@dataclass(frozen=True)
class FlashMaskAttentionResult:
    """Return container for future kernel-backed attention calls."""

    output: Any
    softmax_lse: Any
    backend: str
    requested_backend: str | None = None
    selected_backend: str | None = None
    backend_kind: str | None = None


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
    device_name: str | None = None
    compute_capability: tuple[int, int] | None = None
    capability: tuple[int, int] | None = None
    supported_compute_capabilities: tuple[tuple[int, int], ...] = ()
    requested_backend: str | None = None
    selected_backend: str | None = None
    forward_ready: bool = False
    backward_ready: bool = False
    supports_sm8x: bool = False
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class _BackendResolution:
    requested_backend: str
    canonical_request: str | None
    selected_backend: str | None
    backend_kind: str | None
    unavailable_reason: str | None = None


def backend_info(*, backend: str = "auto") -> BackendInfo:
    """Return backend availability without importing external training stacks.

    The package ships the mask representation, dense reference path, and
    optional backend loader. A real backend must report sparse-mask support for
    the requested architecture; unsupported requests fail closed instead of
    accidentally treating a dense fallback as the fast path.
    """

    requested_backend = str(backend)
    status = extension_status()
    resolution = _resolve_backend(requested_backend, status)
    requested_kind = resolution.backend_kind
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
        and _backend_runtime_is_proven(requested_kind, status.compute_capability)
    )
    known_sparse_backend = bool(is_fa3 or is_fa2_compatible)
    supports_backward = bool(ready and status.backward_ready)
    return BackendInfo(
        name=requested_backend,
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
        device_name=_current_device_name(status),
        compute_capability=status.compute_capability,
        capability=status.compute_capability,
        supported_compute_capabilities=status.supported_compute_capabilities,
        requested_backend=requested_backend,
        selected_backend=resolution.selected_backend,
        forward_ready=bool(ready and status.forward_ready),
        backward_ready=supports_backward,
        supports_sm8x=is_fa2_compatible,
        unavailable_reason=_backend_unavailable_reason(
            resolution=resolution,
            status=status,
            ready=ready,
        ),
    )


def _backend_kind_for_name(backend: str) -> str | None:
    canonical = _CANONICAL_BACKEND_BY_ALIAS.get(backend)
    if canonical == "auto":
        return None
    return _SPARSE_BACKEND_KIND_BY_BACKEND.get(canonical or backend)


def _resolve_backend(backend: str, status: Any) -> _BackendResolution:
    canonical = _CANONICAL_BACKEND_BY_ALIAS.get(backend)
    if canonical is None:
        supported = ", ".join(repr(name) for name in sorted(_CANONICAL_BACKEND_BY_ALIAS))
        return _BackendResolution(
            requested_backend=backend,
            canonical_request=None,
            selected_backend=None,
            backend_kind=None,
            unavailable_reason=f"FlashMask attention backend {backend!r} is unknown; expected one of {supported}",
        )
    if canonical != "auto":
        return _BackendResolution(
            requested_backend=backend,
            canonical_request=canonical,
            selected_backend=canonical,
            backend_kind=_SPARSE_BACKEND_KIND_BY_BACKEND.get(canonical),
        )

    selected_backend = _BACKEND_BY_SPARSE_BACKEND_KIND.get(status.backend_kind)
    selected_kind = status.backend_kind if selected_backend is not None else None
    if selected_backend is None and status.compute_capability == (8, 6):
        selected_backend = "fa2-compatible"
        selected_kind = SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND
    elif selected_backend is None and status.compute_capability == (8, 0):
        selected_backend = "fa2-compatible"
        selected_kind = SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND
    elif selected_backend is None and status.compute_capability == (9, 0):
        selected_backend = "fa3"
        selected_kind = SPARSE_SM90_FA3_BACKEND_KIND
    return _BackendResolution(
        requested_backend=backend,
        canonical_request=canonical,
        selected_backend=selected_backend,
        backend_kind=selected_kind,
    )


def _backend_runtime_is_proven(backend_kind: str | None, capability: tuple[int, int] | None) -> bool:
    if backend_kind is None or capability is None:
        return False
    return tuple(capability) in _PROVEN_COMPUTE_CAPABILITIES_BY_BACKEND_KIND.get(
        backend_kind,
        frozenset(),
    )


def _current_device_name(status: Any) -> str | None:
    if not status.cuda_available:
        return None
    try:
        import torch
    except Exception:
        return None
    try:
        if not torch.cuda.is_available():
            return None
        return str(torch.cuda.get_device_name())
    except Exception:
        return None


def _backend_unavailable_reason(
    *,
    resolution: _BackendResolution,
    status: Any,
    ready: bool,
) -> str | None:
    if ready:
        return None
    if resolution.unavailable_reason is not None:
        return resolution.unavailable_reason
    requested_kind = resolution.backend_kind
    backend = resolution.requested_backend
    selected = resolution.selected_backend
    capability = status.compute_capability
    if requested_kind == SPARSE_SM90_FA3_BACKEND_KIND and capability != (9, 0):
        return f"backend={backend!r} requires SM90 / compute capability 9.0, got {capability}"
    if requested_kind == SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND and capability != (8, 6):
        if capability == (8, 0):
            return (
                f"backend={backend!r} selected {selected!r}, but SM80 runtime proof "
                "is not recorded"
            )
        return f"backend={backend!r} requires verified SM86 / compute capability 8.6, got {capability}"
    if requested_kind and not _backend_runtime_is_proven(requested_kind, capability):
        if requested_kind == SPARSE_SM90_FA3_BACKEND_KIND:
            return (
                f"backend={backend!r} selected {selected!r}, but SM90/Hopper runtime "
                "proof is not recorded"
            )
        return f"backend={backend!r} selected {selected!r}, but runtime proof is not recorded"
    if requested_kind is None:
        return status.unavailable_reason or f"backend={backend!r} could not select a sparse backend"
    if status.backend_kind and status.backend_kind != requested_kind:
        return (
            f"loaded backend kind {status.backend_kind!r} does not match "
            f"requested backend {backend!r}"
        )
    return status.unavailable_reason


def verify_backend(
    *,
    backend: str = "auto",
    require_fa3: bool = False,
    require_sparse: bool = True,
    require_forward: bool = True,
    require_backward: bool = False,
) -> BackendInfo:
    """Verify that a production FlashMask backend is available.

    Raises ``RuntimeError`` until the CUDA/FA3 sparse attention extension is
    installed. This is intentionally separate from dense reference utilities.
    """

    info = backend_info(backend=backend)
    if not info.available:
        raise RuntimeError(
            _format_backend_failure(
                info,
                info.unavailable_reason or f"backend {backend!r} is unavailable",
            )
        )
    if require_forward and not info.forward_ready:
        raise RuntimeError(
            _format_backend_failure(info, f"backend {backend!r} does not support forward")
        )
    if require_fa3 and not info.is_fa3:
        raise RuntimeError(
            _format_backend_failure(info, f"backend {backend!r} is not FA3-compatible")
        )
    if require_sparse and not info.supports_sparse_mask:
        raise RuntimeError(
            _format_backend_failure(
                info,
                f"backend {backend!r} does not support sparse FlashMask metadata",
            )
        )
    if require_backward and not info.supports_backward:
        raise RuntimeError(
            _format_backend_failure(info, f"backend {backend!r} does not support backward")
        )
    return info


def flashmask_attention(
    q: Any,
    k: Any,
    v: Any,
    mask: IntervalMask,
    *,
    backend: str = "auto",
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

    if not isinstance(mask, IntervalMask):
        raise TypeError(
            "flashmask_attention requires an IntervalMask; dense boolean or "
            "additive masks are not accepted by the sparse backend. Use "
            "compile_dense_bool_mask(...) when the dense mask is representable, "
            "or dense reference helpers for tests only."
        )

    status = extension_status()
    resolution = _resolve_backend(str(backend), status)
    if resolution.canonical_request is None:
        supported = ", ".join(repr(name) for name in sorted(_CANONICAL_BACKEND_BY_ALIAS))
        raise ValueError(
            f"FlashMask attention backend must be one of {supported}, got {backend!r}"
        )
    requested_kind = resolution.backend_kind
    _validate_experimental_forward_limits(q, k, v, block_mask)
    needs_backward = any(
        bool(getattr(tensor, "requires_grad", False))
        for tensor in (q, k, v)
    )
    try:
        verify_backend(
            backend=backend,
            require_fa3=resolution.selected_backend == "fa3",
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
    selected_backend = resolution.selected_backend or str(backend)
    return FlashMaskAttentionResult(
        output=output,
        softmax_lse=softmax_lse,
        backend=selected_backend,
        requested_backend=str(backend),
        selected_backend=selected_backend,
        backend_kind=requested_kind,
    )


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

    dtype_names = {
        "q": _dtype_name(q),
        "k": _dtype_name(k),
        "v": _dtype_name(v),
    }
    present_dtypes = {name: dtype for name, dtype in dtype_names.items() if dtype is not None}
    if len(set(present_dtypes.values())) > 1:
        details = ", ".join(f"{name}_dtype={dtype!r}" for name, dtype in present_dtypes.items())
        raise NotImplementedError(f"FlashMask experimental forward requires matching Q/K/V dtypes; {details}")
    for tensor_name, dtype_name in present_dtypes.items():
        if not _supported_attention_dtype(dtype_name):
            raise NotImplementedError(
                "FlashMask experimental forward supports fp16/bf16 only; "
                f"{tensor_name}_dtype={dtype_name!r}"
            )

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


def _dtype_name(tensor: Any) -> str | None:
    dtype = getattr(tensor, "dtype", None)
    if dtype is None:
        return None
    return str(dtype).replace("torch.", "")


def _supported_attention_dtype(dtype_name: str) -> bool:
    return dtype_name in {"float16", "bfloat16", "half"}


def _format_backend_failure(info: BackendInfo, problem: str) -> str:
    requested_kind = _backend_kind_for_name(info.selected_backend or info.requested_backend or "")
    backend_kind_for_hint = requested_kind or info.backend_kind
    fields = [
        problem,
        f"requested_backend={info.requested_backend!r}",
        f"selected_backend={info.selected_backend!r}",
        f"backend_kind={info.backend_kind!r}",
        f"compute_capability={info.compute_capability}",
        f"supported_compute_capabilities={info.supported_compute_capabilities}",
        f"cuda_available={info.cuda_available}",
        f"forward_ready={info.forward_ready}",
        f"backward_ready={info.backward_ready}",
    ]
    if info.unavailable_reason and info.unavailable_reason not in problem:
        fields.append(f"reason={info.unavailable_reason}")
    fields.append(_build_hint_for_backend_kind(backend_kind_for_hint))
    return "; ".join(fields)


def dense_fallback_is_not_fast_path() -> None:
    """Marker helper for callers looking for an explicit dense fallback.

    Dense reconstruction helpers live in ``flashmask.core`` and are intended for
    tests and parity checks only. Production attention should call
    ``flashmask_attention`` and handle its ``NotImplementedError`` until a real
    backend is installed.
    """

    return None
