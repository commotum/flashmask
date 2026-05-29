from __future__ import annotations

import importlib.util
import math
import os

import pytest

import flashmask


SUPPORTED_SM8X_CAPABILITIES = {(8, 0), (8, 6)}


def _require_or_skip(
    reason: str,
    *,
    require_envs: tuple[str, ...] = (
        "FLASHMASK_REQUIRE_SM90",
        "FLASHMASK_REQUIRE_SM8X",
        "FLASHMASK_REQUIRE_SM86",
        "FLASHMASK_REQUIRE_SM80",
    ),
) -> None:
    if any(os.environ.get(env_name) == "1" for env_name in require_envs):
        raise AssertionError(reason)
    pytest.skip(reason)


def _require_torch():
    try:
        import torch
    except Exception as exc:  # pragma: no cover - depends on local environment
        _require_or_skip(f"torch is required for FlashMask CUDA validation: {exc}")
    return torch


def test_compiled_extension_device_gate_when_present():
    if importlib.util.find_spec("flashmask._C") is None:
        _require_or_skip("compiled FlashMask extension is not built")
    torch = _require_torch()
    if not torch.cuda.is_available():
        _require_or_skip("CUDA is not available")

    import flashmask._C as extension

    capability = torch.cuda.get_device_capability()
    capability_tuple = tuple(capability)
    ready = bool(extension.kernel_ready())
    backward_ready = bool(extension.backward_ready())
    backend_kind = str(extension.backend_kind())
    if os.environ.get("FLASHMASK_REQUIRE_SM8X") == "1" and (
        capability_tuple not in SUPPORTED_SM8X_CAPABILITIES
        or backend_kind != flashmask.SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND
        or not ready
    ):
        raise AssertionError(
            "FlashMask SM8x sparse forward requires a ready SM80 or SM86 CUDA device, "
            f"got {capability}"
        )
    if os.environ.get("FLASHMASK_REQUIRE_SM86") == "1" and (
        capability_tuple != (8, 6)
        or backend_kind != flashmask.SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND
        or not ready
    ):
        raise AssertionError(
            "FlashMask SM86 sparse forward requires a ready compute capability 8.6 "
            f"CUDA device, got {capability}"
        )
    if os.environ.get("FLASHMASK_REQUIRE_SM80") == "1" and (
        capability_tuple != (8, 0)
        or backend_kind != flashmask.SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND
        or not ready
    ):
        raise AssertionError(
            "FlashMask SM80 sparse forward requires a ready compute capability 8.0 "
            f"CUDA device, got {capability}"
        )
    if os.environ.get("FLASHMASK_REQUIRE_SM90") == "1" and (
        capability_tuple != (9, 0)
        or backend_kind != flashmask.SPARSE_SM90_FA3_BACKEND_KIND
        or not ready
    ):
        raise AssertionError(
            "FlashMask sparse forward requires a ready SM90 / compute capability 9.0 "
            f"CUDA device, got {capability}"
        )
    if ready:
        assert (
            capability_tuple == (9, 0)
            and backend_kind == flashmask.SPARSE_SM90_FA3_BACKEND_KIND
        ) or (
            capability_tuple in SUPPORTED_SM8X_CAPABILITIES
            and backend_kind == flashmask.SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND
        )
        if hasattr(extension, "supported_compute_capabilities"):
            supported = {tuple(item) for item in extension.supported_compute_capabilities()}
            if backend_kind == flashmask.SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND:
                assert SUPPORTED_SM8X_CAPABILITIES <= supported
            else:
                assert (9, 0) in supported
        if backend_kind == flashmask.SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND:
            assert backward_ready is True
        else:
            assert backward_ready is False

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
            "FlashMask sparse forward requires an SM90 / compute capability 9.0 CUDA device",
            require_envs=("FLASHMASK_REQUIRE_SM90",),
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
    if tuple(capability) not in SUPPORTED_SM8X_CAPABILITIES or not bool(extension.kernel_ready()):
        _require_or_skip(
            "FlashMask SM8x sparse forward requires an SM80 or SM86 CUDA device",
            require_envs=("FLASHMASK_REQUIRE_SM8X", "FLASHMASK_REQUIRE_SM86", "FLASHMASK_REQUIRE_SM80"),
        )
    if os.environ.get("FLASHMASK_REQUIRE_SM86") == "1" and tuple(capability) != (8, 6):
        raise AssertionError(f"FlashMask SM86 sparse forward requires compute capability 8.6, got {capability}")
    if os.environ.get("FLASHMASK_REQUIRE_SM80") == "1" and tuple(capability) != (8, 0):
        raise AssertionError(f"FlashMask SM80 sparse forward requires compute capability 8.0, got {capability}")
    if extension.backend_kind() != flashmask.SPARSE_SM8X_FA2_COMPAT_BACKEND_KIND:
        _require_or_skip(
            "compiled FlashMask extension is not the SM8x sparse backend",
            require_envs=("FLASHMASK_REQUIRE_SM8X", "FLASHMASK_REQUIRE_SM86", "FLASHMASK_REQUIRE_SM80"),
        )
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


def _dense_reference_grads(torch, q, k, v, mask, dout):
    scale = 1.0 / math.sqrt(q.size(-1))
    allowed = torch.tensor(mask.to_bool_mask(nheads=q.size(2)), device=q.device)
    q_ref = q.detach().float().requires_grad_(True)
    k_ref = k.detach().float().requires_grad_(True)
    v_ref = v.detach().float().requires_grad_(True)
    scores = torch.einsum("bqhd,bkhd->bhqk", q_ref, k_ref) * scale
    probs = torch.softmax(scores.masked_fill(~allowed, -torch.inf), dim=-1)
    out = torch.einsum("bhqk,bkhd->bqhd", probs, v_ref)
    loss = (out * dout.detach().float()).sum()
    loss.backward()
    return q_ref.grad, k_ref.grad, v_ref.grad


def _assert_flashmask_raw_matches_dense(torch, q, k, v, mask):
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

    atol = 6e-2 if q.dtype == torch.bfloat16 else 3e-2
    rtol = 6e-2 if q.dtype == torch.bfloat16 else 3e-2
    torch.testing.assert_close(out.float(), expected_out, atol=atol, rtol=rtol)
    torch.testing.assert_close(lse.float(), expected_lse, atol=atol, rtol=rtol)


def _assert_flashmask_raw_backward_matches_dense(torch, q, k, v, mask, *, seed):
    startend = torch.as_tensor(mask.to_list(), device="cuda", dtype=torch.int32)
    block_mask = torch.empty(0, device="cuda", dtype=torch.int32)
    scale = 1.0 / math.sqrt(q.size(-1))
    generator = torch.Generator(device="cuda").manual_seed(seed)
    dout = torch.randn(q.size(0), q.size(1), q.size(2), v.size(3), device="cuda", dtype=q.dtype, generator=generator)

    out, lse = torch.ops.flashmask.fwd(
        q.contiguous(),
        k.contiguous(),
        v.contiguous(),
        startend,
        block_mask,
        scale,
        mask.causal,
    )
    dq, dk, dv = torch.ops.flashmask.bwd(
        dout.contiguous(),
        q.contiguous(),
        k.contiguous(),
        v.contiguous(),
        out.contiguous(),
        lse.contiguous(),
        startend,
        block_mask,
        scale,
        mask.causal,
        False,
    )
    expected_dq, expected_dk, expected_dv = _dense_reference_grads(torch, q, k, v, mask, dout)

    atol = 1.5e-1 if q.dtype == torch.bfloat16 else 6e-2
    rtol = 1.5e-1 if q.dtype == torch.bfloat16 else 6e-2
    torch.testing.assert_close(dq.float(), expected_dq, atol=atol, rtol=rtol)
    torch.testing.assert_close(dk.float(), expected_dk, atol=atol, rtol=rtol)
    torch.testing.assert_close(dv.float(), expected_dv, atol=atol, rtol=rtol)


def _time_cuda_ms(torch, fn, *, warmup=5, iters=30, repeats=3):
    timings = []
    for _ in range(repeats):
        for _ in range(warmup):
            fn()
        torch.cuda.synchronize()
        start = torch.cuda.Event(enable_timing=True)
        end = torch.cuda.Event(enable_timing=True)
        start.record()
        for _ in range(iters):
            fn()
        end.record()
        torch.cuda.synchronize()
        timings.append(start.elapsed_time(end) / iters)
    return min(timings)


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
    _assert_flashmask_raw_matches_dense(torch, q, k, v, mask)


def _sm8x_full_sequence_multibatch_mask():
    return flashmask.compile_pe_state_causal_mask(
        token_type_id=[
            [1, 2, 3, 3, 3, 3, 3, 3],
            [1, 2, 3, 3, 3, 3, 3, 3],
        ],
        time_index=[
            [-1, -1, 1, 1, 2, 3, 3, 4],
            [-1, -1, 1, 2, 2, 3, 4, 4],
        ],
        valid_token=[
            [True, True, True, True, True, True, True, True],
            [True, True, True, True, True, True, True, True],
        ],
        mask_heads=3,
    )


def _sm8x_cached_query_multibatch_mask():
    return flashmask.compile_pe_state_causal_query_mask(
        query_token_type_id=[
            [1, 2, 3, 3],
            [2, 3, 3, 3],
        ],
        query_time_index=[
            [-1, -1, 1, 2],
            [-1, 1, 2, 3],
        ],
        key_token_type_id=[
            [1, 2, 3, 3, 3, 0],
            [1, 2, 3, 3, 3, 0],
        ],
        key_time_index=[
            [-1, -1, 1, 2, 3, -1],
            [-1, -1, 1, 2, 3, -1],
        ],
        key_valid_token=[
            [True, True, True, True, False, False],
            [True, True, True, True, True, False],
        ],
        mask_heads=2,
    )


@pytest.mark.parametrize(
    ("case_name", "dtype_name", "heads", "head_dim", "seed", "mask_factory"),
    [
        ("full_sequence_multibatch_broadcast", "float16", 6, 96, 89, _sm8x_full_sequence_multibatch_mask),
        ("cached_query_multibatch_bf16", "bfloat16", 4, 128, 90, _sm8x_cached_query_multibatch_mask),
    ],
)
def test_optional_sm8x_raw_op_matches_dense_reference_matrix(
    case_name,
    dtype_name,
    heads,
    head_dim,
    seed,
    mask_factory,
):
    torch = _require_sm8x_raw_op()
    dtype = getattr(torch, dtype_name)
    mask = mask_factory()
    generator = torch.Generator(device="cuda").manual_seed(seed)
    batch, mask_heads, seqlen_k, _bound_num = mask.shape
    assert heads % mask_heads == 0
    q = torch.randn(
        batch,
        mask.seqlen_q,
        heads,
        head_dim,
        device="cuda",
        dtype=dtype,
        generator=generator,
    )
    k = torch.randn(
        batch,
        seqlen_k,
        heads,
        head_dim,
        device="cuda",
        dtype=dtype,
        generator=generator,
    )
    v = torch.randn(
        batch,
        seqlen_k,
        heads,
        head_dim,
        device="cuda",
        dtype=dtype,
        generator=generator,
    )

    _assert_flashmask_raw_matches_dense(torch, q, k, v, mask)


def test_optional_sm8x_rejects_unsupported_mask_kinds():
    torch = _require_sm8x_raw_op()
    q = torch.randn(1, 4, 1, 64, device="cuda", dtype=torch.float16)
    k = torch.randn(1, 4, 1, 64, device="cuda", dtype=torch.float16)
    v = torch.randn(1, 4, 1, 64, device="cuda", dtype=torch.float16)
    block_mask = torch.empty(0, device="cuda", dtype=torch.int32)

    causal = flashmask.causal_mask(4)
    causal_startend = torch.as_tensor(causal.to_list(), device="cuda", dtype=torch.int32)
    with pytest.raises(RuntimeError, match="non-causal interval masks only"):
        torch.ops.flashmask.fwd(q, k, v, causal_startend, block_mask, float("nan"), causal.causal)

    bound4 = flashmask.from_dense_bool_mask(
        [
            [False, True, False, True],
            [True, True, True, True],
            [False, True, False, True],
            [True, True, True, True],
        ],
        bound_num=4,
    )
    bound4_startend = torch.as_tensor(bound4.to_list(), device="cuda", dtype=torch.int32)
    with pytest.raises(RuntimeError, match="bound_num=2 masks only"):
        torch.ops.flashmask.fwd(q, k, v, bound4_startend, block_mask, float("nan"), bound4.causal)


@pytest.mark.parametrize(
    ("case_name", "dtype_name", "heads", "head_dim", "seed", "mask_factory"),
    [
        ("full_sequence_multibatch_broadcast", "float16", 6, 96, 101, _sm8x_full_sequence_multibatch_mask),
        ("cached_query_multibatch_bf16", "bfloat16", 4, 128, 102, _sm8x_cached_query_multibatch_mask),
    ],
)
def test_optional_sm8x_raw_backward_matches_dense_reference_matrix(
    case_name,
    dtype_name,
    heads,
    head_dim,
    seed,
    mask_factory,
):
    torch = _require_sm8x_raw_op()

    import flashmask._C as extension

    assert bool(extension.backward_ready()) is True

    dtype = getattr(torch, dtype_name)
    mask = mask_factory()
    generator = torch.Generator(device="cuda").manual_seed(seed)
    batch, mask_heads, seqlen_k, _bound_num = mask.shape
    assert heads % mask_heads == 0
    q = torch.randn(
        batch,
        mask.seqlen_q,
        heads,
        head_dim,
        device="cuda",
        dtype=dtype,
        generator=generator,
    )
    k = torch.randn(
        batch,
        seqlen_k,
        heads,
        head_dim,
        device="cuda",
        dtype=dtype,
        generator=generator,
    )
    v = torch.randn(
        batch,
        seqlen_k,
        heads,
        head_dim,
        device="cuda",
        dtype=dtype,
        generator=generator,
    )

    _assert_flashmask_raw_backward_matches_dense(torch, q, k, v, mask, seed=seed + 1000)


def test_optional_sm8x_public_autograd_matches_dense_reference():
    torch = _require_sm8x_raw_op()

    import flashmask._C as extension

    assert bool(extension.backward_ready()) is True

    generator = torch.Generator(device="cuda").manual_seed(87)
    q = torch.randn(1, 6, 2, 64, device="cuda", dtype=torch.float16, generator=generator).requires_grad_(True)
    k = torch.randn(1, 6, 2, 64, device="cuda", dtype=torch.float16, generator=generator).requires_grad_(True)
    v = torch.randn(1, 6, 2, 64, device="cuda", dtype=torch.float16, generator=generator).requires_grad_(True)
    dout = torch.randn_like(q)
    mask = flashmask.compile_pe_state_causal_mask(
        token_type_id=[[1, 2, 3, 3, 3, 3]],
        time_index=[[-1, -1, 0, 0, 1, 2]],
        valid_token=[[True, True, True, True, True, True]],
        mask_heads=1,
    )

    info = flashmask.verify_backend(
        backend="fa2-compatible",
        require_fa3=False,
        require_sparse=True,
        require_backward=True,
    )
    assert info.supports_backward is True
    result = flashmask.flashmask_attention(q, k, v, mask, backend="fa2-compatible")
    (result.output.float() * dout.float()).sum().backward()

    expected_dq, expected_dk, expected_dv = _dense_reference_grads(torch, q.detach(), k.detach(), v.detach(), mask, dout)
    torch.testing.assert_close(q.grad.float(), expected_dq, atol=6e-2, rtol=6e-2)
    torch.testing.assert_close(k.grad.float(), expected_dk, atol=6e-2, rtol=6e-2)
    torch.testing.assert_close(v.grad.float(), expected_dv, atol=6e-2, rtol=6e-2)


def test_optional_sm8x_profiles_flashmask_sparse_kernels():
    torch = _require_sm8x_raw_op()

    generator = torch.Generator(device="cuda").manual_seed(88)
    q = torch.randn(1, 6, 2, 64, device="cuda", dtype=torch.float16, generator=generator)
    k = torch.randn(1, 6, 2, 64, device="cuda", dtype=torch.float16, generator=generator)
    v = torch.randn(1, 6, 2, 64, device="cuda", dtype=torch.float16, generator=generator)
    mask = flashmask.compile_pe_state_causal_mask(
        token_type_id=[[1, 2, 3, 3, 3, 3]],
        time_index=[[-1, -1, 0, 0, 1, 2]],
        valid_token=[[True, True, True, True, True, True]],
        mask_heads=1,
    )
    startend = torch.as_tensor(mask.to_list(), device="cuda", dtype=torch.int32)
    block_mask = torch.empty(0, device="cuda", dtype=torch.int32)
    scale = 1.0 / math.sqrt(q.size(-1))

    for _ in range(3):
        torch.ops.flashmask.fwd(q, k, v, startend, block_mask, scale, mask.causal)
    torch.cuda.synchronize()

    with torch.profiler.profile(
        activities=[
            torch.profiler.ProfilerActivity.CPU,
            torch.profiler.ProfilerActivity.CUDA,
        ]
    ) as profiler:
        torch.ops.flashmask.fwd(q, k, v, startend, block_mask, scale, mask.causal)
    torch.cuda.synchronize()

    event_names = {event.key for event in profiler.key_averages()}
    assert "flashmask::fwd" in event_names
    assert any("scanMaxMin" in name for name in event_names)
    assert any("cutlass_flashmask_kernel" in name for name in event_names)
    assert not any("scaled_dot_product" in name for name in event_names)
    assert not any(name in {"aten::matmul", "aten::softmax"} for name in event_names)


def test_optional_sm8x_profiles_flashmask_sparse_backward_kernel():
    torch = _require_sm8x_raw_op()

    generator = torch.Generator(device="cuda").manual_seed(92)
    q = torch.randn(1, 6, 2, 64, device="cuda", dtype=torch.float16, generator=generator)
    k = torch.randn(1, 6, 2, 64, device="cuda", dtype=torch.float16, generator=generator)
    v = torch.randn(1, 6, 2, 64, device="cuda", dtype=torch.float16, generator=generator)
    dout = torch.randn_like(q)
    mask = flashmask.compile_pe_state_causal_mask(
        token_type_id=[[1, 2, 3, 3, 3, 3]],
        time_index=[[-1, -1, 0, 0, 1, 2]],
        valid_token=[[True, True, True, True, True, True]],
        mask_heads=1,
    )
    startend = torch.as_tensor(mask.to_list(), device="cuda", dtype=torch.int32)
    block_mask = torch.empty(0, device="cuda", dtype=torch.int32)
    scale = 1.0 / math.sqrt(q.size(-1))
    out, lse = torch.ops.flashmask.fwd(q, k, v, startend, block_mask, scale, mask.causal)

    for _ in range(3):
        torch.ops.flashmask.bwd(dout, q, k, v, out, lse, startend, block_mask, scale, mask.causal, False)
    torch.cuda.synchronize()

    with torch.profiler.profile(
        activities=[
            torch.profiler.ProfilerActivity.CPU,
            torch.profiler.ProfilerActivity.CUDA,
        ]
    ) as profiler:
        torch.ops.flashmask.bwd(dout, q, k, v, out, lse, startend, block_mask, scale, mask.causal, False)
    torch.cuda.synchronize()

    event_names = {event.key for event in profiler.key_averages()}
    assert "flashmask::bwd" in event_names
    assert any("flashmask_sm8x_backward" in name for name in event_names)
    assert not any("scaled_dot_product" in name for name in event_names)
    assert not any(name in {"aten::matmul", "aten::softmax"} for name in event_names)


def test_optional_sm8x_performance_sanity_tracks_mask_sparsity():
    torch = _require_sm8x_raw_op()
    batch, seqlen_q, seqlen_k, heads, head_dim = 1, 128, 2048, 2, 64
    generator = torch.Generator(device="cuda").manual_seed(91)
    q = torch.randn(
        batch, seqlen_q, heads, head_dim, device="cuda", dtype=torch.float16, generator=generator
    )
    k = torch.randn(
        batch, seqlen_k, heads, head_dim, device="cuda", dtype=torch.float16, generator=generator
    )
    v = torch.randn(
        batch, seqlen_k, heads, head_dim, device="cuda", dtype=torch.float16, generator=generator
    )
    block_mask = torch.empty(0, device="cuda", dtype=torch.int32)
    scale = 1.0 / math.sqrt(head_dim)

    def interval_mask(allowed_k_blocks):
        bounds = [
            [seqlen_q, 0] if key_idx < allowed_k_blocks * 64 else [0, 0]
            for key_idx in range(seqlen_k)
        ]
        return flashmask.IntervalMask([[bounds]], causal=False, seqlen_q=seqlen_q)

    sparse_mask = interval_mask(allowed_k_blocks=4)
    dense_equiv_mask = interval_mask(allowed_k_blocks=seqlen_k // 64)
    sparse_startend = torch.as_tensor(sparse_mask.to_list(), device="cuda", dtype=torch.int32)
    dense_equiv_startend = torch.as_tensor(dense_equiv_mask.to_list(), device="cuda", dtype=torch.int32)
    sparse_allowed = torch.tensor(sparse_mask.to_bool_mask(nheads=heads), device="cuda")

    def sparse_flashmask():
        return torch.ops.flashmask.fwd(q, k, v, sparse_startend, block_mask, scale, False)

    def dense_equiv_flashmask():
        return torch.ops.flashmask.fwd(q, k, v, dense_equiv_startend, block_mask, scale, False)

    def dense_reference():
        scores = torch.einsum("bqhd,bkhd->bhqk", q.float(), k.float()) * scale
        probs = torch.softmax(scores.masked_fill(~sparse_allowed, -torch.inf), dim=-1)
        return torch.einsum("bhqk,bkhd->bqhd", probs, v.float())

    sparse_ms = _time_cuda_ms(torch, sparse_flashmask)
    dense_equiv_ms = _time_cuda_ms(torch, dense_equiv_flashmask)
    dense_reference_ms = _time_cuda_ms(torch, dense_reference, iters=10)

    assert sparse_ms < dense_equiv_ms * 0.90, (
        f"sparse interval path did not get cheaper with masked K blocks: "
        f"sparse={sparse_ms:.4f}ms dense_equiv={dense_equiv_ms:.4f}ms"
    )
    assert sparse_ms < dense_reference_ms, (
        f"sparse interval path is slower than dense reference: "
        f"sparse={sparse_ms:.4f}ms dense_reference={dense_reference_ms:.4f}ms"
    )


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
