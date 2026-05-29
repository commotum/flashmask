from __future__ import annotations

import importlib.util
from pathlib import Path

from flashmask import (
    MaskNotRepresentableError,
    compile_dense_bool_mask,
    document_mask,
    sliding_window_mask,
)


def _context_masks():
    path = Path(__file__).resolve().parents[1] / "context" / "masks.py"
    spec = importlib.util.spec_from_file_location("flashmask_context_masks", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _bool(mask):
    return [[bool(value) for value in row] for row in mask]


def test_sliding_window_builder_matches_context_mask():
    context = _context_masks()
    expected = _bool(context.mask_2_sliding_window)

    mask = sliding_window_mask(10, 3)

    assert mask.to_bool_mask()[0][0] == expected


def test_document_builder_matches_context_mask():
    context = _context_masks()
    expected = _bool(context.mask_4_document)

    mask = document_mask([4, 3, 3])

    assert mask.to_bool_mask()[0][0] == expected


def test_dense_compiler_represents_global_sliding_context_mask_with_bound4():
    context = _context_masks()
    expected = _bool(context.mask_6_global_sliding)

    mask = compile_dense_bool_mask(expected)

    assert mask.bound_num == 4
    assert mask.to_bool_mask()[0][0] == expected


def test_dense_compiler_either_reconstructs_or_flags_random_eviction_context_mask():
    context = _context_masks()
    dense = _bool(context.mask_12_random_eviction)

    try:
        mask = compile_dense_bool_mask(dense)
    except MaskNotRepresentableError:
        return

    assert mask.to_bool_mask()[0][0] == dense


def test_dense_compiler_reconstructs_all_context_masks():
    context = _context_masks()
    names = sorted(name for name in dir(context) if name.startswith("mask_"))

    assert names
    for name in names:
        expected = _bool(getattr(context, name))
        mask = compile_dense_bool_mask(expected)
        assert mask.to_bool_mask()[0][0] == expected, name
