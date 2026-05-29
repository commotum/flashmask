"""Standalone FlashMask interval-mask API."""

from .attention import (
    BackendInfo,
    FlashMaskAttentionResult,
    backend_info,
    flashmask_attention,
    verify_backend,
)
from .builders import (
    causal_mask,
    document_mask,
    from_dense_bool_mask,
    prefix_lm_mask,
    sliding_window_mask,
)
from .core import (
    IntervalMask,
    MaskNotRepresentableError,
    compile_dense_bool_mask,
    dense_additive_from_intervals,
    dense_bool_from_intervals,
    normalize_dense_bool_mask,
    normalize_startend_row_indices,
)
from .pe import (
    DEFAULT_PE_TOKEN_TYPES,
    NO_STATE_TIME,
    PETokenTypeIds,
    compile_pe_state_causal_mask,
    compile_pe_state_causal_query_mask,
    dense_pe_state_causal_mask,
    dense_pe_state_causal_query_mask,
)

__all__ = [
    "DEFAULT_PE_TOKEN_TYPES",
    "BackendInfo",
    "FlashMaskAttentionResult",
    "IntervalMask",
    "MaskNotRepresentableError",
    "NO_STATE_TIME",
    "PETokenTypeIds",
    "backend_info",
    "causal_mask",
    "compile_dense_bool_mask",
    "compile_pe_state_causal_mask",
    "compile_pe_state_causal_query_mask",
    "dense_additive_from_intervals",
    "dense_bool_from_intervals",
    "dense_pe_state_causal_mask",
    "dense_pe_state_causal_query_mask",
    "document_mask",
    "flashmask_attention",
    "from_dense_bool_mask",
    "normalize_dense_bool_mask",
    "normalize_startend_row_indices",
    "prefix_lm_mask",
    "sliding_window_mask",
    "verify_backend",
]
