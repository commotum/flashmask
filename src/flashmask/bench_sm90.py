"""SM90 sparse-backend parity and benchmark harness."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from itertools import product
from pathlib import Path
from typing import Any

from .pe import (
    DEFAULT_PE_TOKEN_TYPES,
    NO_STATE_TIME,
    compile_pe_state_causal_mask,
    compile_pe_state_causal_query_mask,
)


DTYPE_ALIASES = {
    "fp16": "float16",
    "float16": "float16",
    "bf16": "bfloat16",
    "bfloat16": "bfloat16",
}

REQUIRED_FLASHMASK_CUDA_KERNEL_MARKERS = (
    "prepare_flashmask_kernel",
    "scanMaxMinChunkedKernel",
    "cutlass_flashmask_kernel",
)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        _validate_args(args)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        torch = _import_torch()
    except RuntimeError as exc:
        return _skip(str(exc), args.require_sm90, output_jsonl=args.output_jsonl)

    gate = _backend_gate(torch)
    if gate["status"] != "ok":
        return _skip(gate["reason"], args.require_sm90, gate, output_jsonl=args.output_jsonl)

    from .attention import flashmask_attention

    common = _common_record(torch, gate)
    profile_state: dict[
        tuple[Any, ...],
        tuple[bool, tuple[str, ...], tuple[str, ...], tuple[str, ...]],
    ] = {}
    records = []

    def record_case(
        *,
        case: str,
        batch: int,
        seqlen: int,
        query_len: int,
        heads: int,
        mask_heads: int,
        head_dim: int,
        dtype_name: str,
        pad_fraction: float,
        benchmark: bool,
    ) -> bool:
        try:
            records.append(
                _run_case(
                    torch,
                    flashmask_attention,
                    args,
                    common,
                    case=case,
                    batch=batch,
                    seqlen=seqlen,
                    query_len=query_len,
                    heads=heads,
                    mask_heads=mask_heads,
                    head_dim=head_dim,
                    dtype_name=dtype_name,
                    pad_fraction=pad_fraction,
                    benchmark=benchmark,
                    profile_state=profile_state,
                )
            )
            return True
        except Exception as exc:
            records.append(
                _failed_record(
                    common,
                    error=str(exc),
                    case=case,
                    batch=batch,
                    seqlen=seqlen,
                    query_len=query_len,
                    heads=heads,
                    mask_heads=mask_heads,
                    head_dim=head_dim,
                    dtype_name=dtype_name,
                    pad_fraction=pad_fraction,
                    benchmark=benchmark,
                )
            )
            _emit_records(records, jsonl=args.jsonl, output_jsonl=args.output_jsonl)
            return False

    if args.mode in ("all", "parity"):
        for dtype_name, head_dim in product(_csv(args.dtypes), _int_csv(args.head_dims)):
            if not record_case(
                    case="full",
                    batch=args.batch,
                    seqlen=args.parity_seqlen,
                    query_len=args.parity_seqlen,
                    heads=args.heads,
                    mask_heads=args.mask_heads,
                    head_dim=head_dim,
                    dtype_name=DTYPE_ALIASES[dtype_name],
                    pad_fraction=0.0,
                    benchmark=False,
            ):
                return 1
            if not record_case(
                    case="full_padded",
                    batch=args.batch,
                    seqlen=args.parity_seqlen,
                    query_len=args.parity_seqlen,
                    heads=args.heads,
                    mask_heads=args.mask_heads,
                    head_dim=head_dim,
                    dtype_name=DTYPE_ALIASES[dtype_name],
                    pad_fraction=0.5,
                    benchmark=False,
            ):
                return 1
            if not record_case(
                    case="query_block",
                    batch=args.batch,
                    seqlen=args.query_key_seqlen,
                    query_len=args.query_seqlen,
                    heads=args.heads,
                    mask_heads=args.mask_heads,
                    head_dim=head_dim,
                    dtype_name=DTYPE_ALIASES[dtype_name],
                    pad_fraction=0.0,
                    benchmark=False,
            ):
                return 1

    if args.mode in ("all", "bench"):
        for dtype_name, head_dim, seqlen, pad_fraction in product(
            _csv(args.dtypes),
            _int_csv(args.head_dims),
            _int_csv(args.bench_seq_lens),
            _float_csv(args.pad_fractions),
        ):
            if not record_case(
                    case="bench",
                    batch=args.batch,
                    seqlen=seqlen,
                    query_len=seqlen,
                    heads=args.heads,
                    mask_heads=args.mask_heads,
                    head_dim=head_dim,
                    dtype_name=DTYPE_ALIASES[dtype_name],
                    pad_fraction=pad_fraction,
                    benchmark=True,
            ):
                return 1

    _emit_records(records, jsonl=args.jsonl, output_jsonl=args.output_jsonl)
    return 0 if all(record.get("status") == "ok" for record in records) else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and benchmark the FlashMask SM90 sparse forward path."
    )
    parser.add_argument("--mode", choices=("all", "parity", "bench"), default="all")
    parser.add_argument("--batch", type=int, default=1)
    parser.add_argument("--heads", type=int, default=4)
    parser.add_argument("--mask-heads", type=int, default=1)
    parser.add_argument("--dtypes", default="fp16,bf16")
    parser.add_argument("--head-dims", default="96,128")
    parser.add_argument("--parity-seqlen", type=int, default=512)
    parser.add_argument("--query-seqlen", type=int, default=128)
    parser.add_argument("--query-key-seqlen", type=int, default=1024)
    parser.add_argument("--bench-seq-lens", default="2048,4096")
    parser.add_argument("--pad-fractions", default="0.0,0.5")
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--iters", type=int, default=50)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--atol-fp16", type=float, default=3e-2)
    parser.add_argument("--rtol-fp16", type=float, default=3e-2)
    parser.add_argument("--atol-bf16", type=float, default=6e-2)
    parser.add_argument("--rtol-bf16", type=float, default=6e-2)
    parser.add_argument("--min-speedup", type=float, default=None)
    parser.add_argument(
        "--output-jsonl",
        default=None,
        help="write benchmark records to this JSONL file in addition to stdout",
    )
    parser.add_argument(
        "--skip-profiler-check",
        action="store_true",
        help="skip the one-shot profiler check for the flashmask::fwd op name",
    )
    parser.add_argument("--jsonl", action="store_true")
    parser.add_argument(
        "--require-sm90",
        action="store_true",
        help="raise an error instead of exiting cleanly when SM90 backend is unavailable",
    )
    return parser


def _import_torch():
    try:
        import torch
    except Exception as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError("the SM90 benchmark requires PyTorch") from exc
    return torch


def _backend_gate(torch: Any) -> dict[str, Any]:
    if not torch.cuda.is_available():
        return {"status": "skipped", "reason": "CUDA is not available"}
    try:
        import flashmask._C as extension
    except Exception as exc:
        return {"status": "skipped", "reason": f"compiled extension unavailable: {exc}"}

    import flashmask

    info = flashmask.backend_info()
    capability = torch.cuda.get_device_capability()
    ready = bool(extension.kernel_ready())
    forward_ready = bool(getattr(extension, "forward_ready", lambda: ready)())
    backend_kind = getattr(extension, "backend_kind", lambda: None)()
    if tuple(capability) != (9, 0):
        return {
            "status": "skipped",
            "reason": f"SM90 / compute capability 9.0 CUDA device required, found {capability}",
            "capability": capability,
            "kernel_ready": ready,
            "forward_ready": forward_ready,
            "backend_kind": backend_kind,
        }
    if (
        not ready
        or not forward_ready
        or backend_kind != "sm90_sparse_fa3"
        or not info.available
        or not info.is_fa3
        or not info.supports_sparse_mask
    ):
        return {
            "status": "skipped",
            "reason": "compiled sparse FA3 backend is not ready",
            "capability": capability,
            "kernel_ready": ready,
            "forward_ready": forward_ready,
            "backend_kind": backend_kind,
        }
    return {
        "status": "ok",
        "capability": capability,
        "kernel_ready": ready,
        "forward_ready": forward_ready,
        "backend_kind": backend_kind,
        "module_path": info.module_path,
    }


def _skip(
    reason: str,
    require_sm90: bool,
    extra: dict[str, Any] | None = None,
    *,
    output_jsonl: str | None = None,
) -> int:
    if require_sm90:
        raise RuntimeError(reason)
    record = {"status": "skipped", "reason": reason}
    if extra:
        record.update({key: _jsonable(value) for key, value in extra.items()})
    _emit_records([record], jsonl=True, output_jsonl=output_jsonl)
    return 0


def _common_record(torch: Any, gate: dict[str, Any]) -> dict[str, Any]:
    return {
        "device": torch.cuda.get_device_name(),
        "capability": list(gate["capability"]),
        "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda,
        "_C_path": gate.get("module_path"),
        "kernel_ready": bool(gate["kernel_ready"]),
        "forward_ready": bool(gate["forward_ready"]),
        "backend_kind": gate.get("backend_kind"),
    }


def _failed_record(
    common: dict[str, Any],
    *,
    error: str,
    case: str,
    batch: int,
    seqlen: int,
    query_len: int,
    heads: int,
    mask_heads: int,
    head_dim: int,
    dtype_name: str,
    pad_fraction: float,
    benchmark: bool,
) -> dict[str, Any]:
    return {
        **common,
        "status": "failed",
        "backend": "fa3",
        "error": error,
        "case": case,
        "B": batch,
        "H": heads,
        "mask_heads": mask_heads,
        "Q": query_len,
        "K": seqlen,
        "D": head_dim,
        "dtype": dtype_name,
        "pad_fraction": pad_fraction,
        "benchmark": benchmark,
        "passed": False,
    }


def _run_case(
    torch: Any,
    flashmask_attention: Any,
    args: argparse.Namespace,
    common: dict[str, Any],
    *,
    case: str,
    batch: int,
    seqlen: int,
    query_len: int,
    heads: int,
    mask_heads: int,
    head_dim: int,
    dtype_name: str,
    pad_fraction: float,
    benchmark: bool,
    profile_state: dict[
        tuple[Any, ...],
        tuple[bool, tuple[str, ...], tuple[str, ...], tuple[str, ...]],
    ],
) -> dict[str, Any]:
    if heads % mask_heads != 0:
        raise ValueError("heads must be divisible by mask_heads")
    torch.manual_seed(args.seed)
    dtype = getattr(torch, dtype_name)
    mask = _mask(batch, seqlen, query_len, mask_heads, pad_fraction)
    q, k, v = _qkv(torch, batch, query_len, seqlen, heads, head_dim, dtype, args.seed)
    scale = 1.0 / math.sqrt(head_dim)
    expected_out, expected_lse, additive_mask, density = _dense_reference(torch, q, k, v, mask, scale)

    result = flashmask_attention(q, k, v, mask, softmax_scale=scale)
    if result.backend != "fa3":
        raise RuntimeError(f"expected fa3 backend, got {result.backend!r}")
    if tuple(result.output.shape) != tuple(q.shape):
        raise RuntimeError(f"unexpected output shape {tuple(result.output.shape)}")
    if tuple(result.softmax_lse.shape) != (batch, heads, query_len):
        raise RuntimeError(f"unexpected LSE shape {tuple(result.softmax_lse.shape)}")
    profile_key = (
        case,
        batch,
        heads,
        mask_heads,
        dtype_name,
        head_dim,
        query_len,
        seqlen,
        pad_fraction,
        mask.causal,
        mask.bound_num,
        _mask_digest(mask),
    )
    profiler_dense_events: tuple[str, ...] = ()
    profiler_missing_cuda_kernel_markers: tuple[str, ...] = ()
    profiler_flashmask_cuda_kernel_events: tuple[str, ...] = ()
    profiler_check_skipped = bool(args.skip_profiler_check)
    if not args.skip_profiler_check:
        if profile_key not in profile_state:
            profile_state[profile_key] = _profile_sparse_attention(
                torch,
                lambda: flashmask_attention(q, k, v, mask, softmax_scale=scale).output,
            )
        (
            has_flashmask_fwd,
            profiler_dense_events,
            profiler_missing_cuda_kernel_markers,
            profiler_flashmask_cuda_kernel_events,
        ) = profile_state[profile_key]
        if not has_flashmask_fwd:
            raise RuntimeError("torch profiler did not observe flashmask::fwd")
        if profiler_missing_cuda_kernel_markers:
            raise RuntimeError(
                "torch profiler did not observe required FlashMask CUDA kernels: "
                + ", ".join(profiler_missing_cuda_kernel_markers)
            )
        if profiler_dense_events:
            raise RuntimeError(
                "torch profiler observed dense attention events: "
                + ", ".join(profiler_dense_events)
            )
    else:
        has_flashmask_fwd = None

    out_delta = (result.output.float() - expected_out).abs()
    lse_delta = (result.softmax_lse.float() - expected_lse).abs()
    out_max_abs = out_delta.max().item()
    lse_max_abs = lse_delta.max().item()
    out_max_rel = (out_delta / expected_out.abs().clamp_min(1e-6)).max().item()
    lse_max_rel = (lse_delta / expected_lse.abs().clamp_min(1e-6)).max().item()
    atol = args.atol_bf16 if dtype_name == "bfloat16" else args.atol_fp16
    rtol = args.rtol_bf16 if dtype_name == "bfloat16" else args.rtol_fp16
    try:
        torch.testing.assert_close(result.output.float(), expected_out, atol=atol, rtol=rtol)
        torch.testing.assert_close(result.softmax_lse.float(), expected_lse, atol=atol, rtol=rtol)
        passed = not profiler_check_skipped
    except AssertionError as exc:
        raise RuntimeError(
            f"parity failed for {case}: out_abs={out_max_abs:.6g}, lse_abs={lse_max_abs:.6g}"
        ) from exc

    record: dict[str, Any] = {
        **common,
        "status": "ok" if passed else "profiler_skipped",
        "backend": "fa3",
        "profiler_check_skipped": profiler_check_skipped,
        "profiler_flashmask_fwd": has_flashmask_fwd,
        "profiler_flashmask_cuda_kernel_events": list(profiler_flashmask_cuda_kernel_events),
        "profiler_missing_flashmask_cuda_kernel_markers": list(
            profiler_missing_cuda_kernel_markers
        ),
        "profiler_dense_attention_events": list(profiler_dense_events),
        "case": case,
        "B": batch,
        "H": heads,
        "mask_heads": mask_heads,
        "Q": query_len,
        "K": seqlen,
        "D": head_dim,
        "dtype": dtype_name,
        "pad_fraction": pad_fraction,
        "allowed_density": density,
        "mask_causal": mask.causal,
        "bound_num": mask.bound_num,
        "out_max_abs": out_max_abs,
        "out_max_rel": out_max_rel,
        "lse_max_abs": lse_max_abs,
        "lse_max_rel": lse_max_rel,
        "atol": atol,
        "rtol": rtol,
        "min_speedup": args.min_speedup,
        "warmup": args.warmup if benchmark else 0,
        "iters": args.iters if benchmark else 0,
        "passed": passed,
    }
    if benchmark:
        if args.min_speedup is None:
            raise RuntimeError("--min-speedup is required for benchmark mode")
        startend = torch.as_tensor(mask.to_list(), device=q.device, dtype=torch.int32)
        block_mask = torch.empty(0, device=q.device, dtype=torch.int32)
        flash_raw_ms = _time_cuda(
            torch,
            lambda: torch.ops.flashmask.fwd(q, k, v, startend, block_mask, scale, mask.causal)[0],
            warmup=args.warmup,
            iters=args.iters,
        )
        flash_api_ms = _time_cuda(
            torch,
            lambda: flashmask_attention(q, k, v, mask, softmax_scale=scale).output,
            warmup=args.warmup,
            iters=args.iters,
        )
        dense_ms = _time_cuda(
            torch,
            lambda: _dense_sdpa(torch, q, k, v, additive_mask),
            warmup=args.warmup,
            iters=args.iters,
        )
        speedup = dense_ms / flash_api_ms
        raw_speedup = dense_ms / flash_raw_ms
        record.update(
            {
                "flashmask_ms": flash_api_ms,
                "flashmask_api_ms": flash_api_ms,
                "flashmask_raw_ms": flash_raw_ms,
                "dense_sdpa_ms": dense_ms,
                "speedup": speedup,
                "raw_speedup": raw_speedup,
            }
        )
        if args.min_speedup is not None and speedup < args.min_speedup:
            raise RuntimeError(f"speedup {speedup:.4f} is below required {args.min_speedup:.4f}")
    else:
        record.update({
            "flashmask_ms": None,
            "flashmask_api_ms": None,
            "flashmask_raw_ms": None,
            "dense_sdpa_ms": None,
            "speedup": None,
            "raw_speedup": None,
        })
    return record


def _mask(batch: int, seqlen: int, query_len: int, mask_heads: int, pad_fraction: float):
    valid_len = seqlen - int(seqlen * pad_fraction)
    valid_len = max(2, min(seqlen, valid_len))
    if query_len == seqlen:
        token_type, time_index, valid_token = _metadata(batch, seqlen, valid_len)
        return compile_pe_state_causal_mask(
            token_type,
            time_index,
            valid_token,
            mask_heads=mask_heads,
        )

    token_types = DEFAULT_PE_TOKEN_TYPES
    key_type, key_time, key_valid = _metadata(batch, seqlen, valid_len)
    query_type = [[token_types.state] * query_len for _ in range(batch)]
    query_time = [list(range(query_len)) for _ in range(batch)]
    return compile_pe_state_causal_query_mask(
        query_type,
        query_time,
        key_type,
        key_time,
        key_valid,
        mask_heads=mask_heads,
    )


def _metadata(batch: int, seqlen: int, valid_len: int):
    if seqlen < 3:
        raise ValueError("seqlen must be at least 3 for BOS, domain, and state tokens")
    token_types = DEFAULT_PE_TOKEN_TYPES
    row_type = [token_types.bos, token_types.domain] + [token_types.state] * (seqlen - 2)
    row_time = [NO_STATE_TIME, NO_STATE_TIME] + list(range(seqlen - 2))
    row_valid = [idx < valid_len for idx in range(seqlen)]
    return (
        [row_type for _ in range(batch)],
        [row_time for _ in range(batch)],
        [row_valid for _ in range(batch)],
    )


def _qkv(
    torch: Any,
    batch: int,
    query_len: int,
    key_len: int,
    heads: int,
    head_dim: int,
    dtype: Any,
    seed: int,
):
    generator = torch.Generator(device="cuda").manual_seed(seed)
    q = torch.randn((batch, query_len, heads, head_dim), device="cuda", dtype=dtype, generator=generator)
    k = torch.randn((batch, key_len, heads, head_dim), device="cuda", dtype=dtype, generator=generator)
    v = torch.randn((batch, key_len, heads, head_dim), device="cuda", dtype=dtype, generator=generator)
    return q.contiguous(), k.contiguous(), v.contiguous()


def _dense_reference(torch: Any, q: Any, k: Any, v: Any, mask: Any, scale: float):
    allowed = torch.tensor(mask.to_bool_mask(nheads=q.size(2)), device=q.device)
    additive_mask = torch.zeros_like(allowed, dtype=torch.float32)
    additive_mask = additive_mask.masked_fill(~allowed, -torch.inf)
    scores = torch.einsum("bqhd,bkhd->bhqk", q.float(), k.float()) * scale
    scores = scores + additive_mask
    probs = torch.softmax(scores, dim=-1)
    out = torch.einsum("bhqk,bkhd->bqhd", probs, v.float())
    density = allowed.float().mean().item()
    return out, torch.logsumexp(scores, dim=-1), additive_mask, density


def _dense_sdpa(torch: Any, q: Any, k: Any, v: Any, additive_mask: Any):
    return torch.nn.functional.scaled_dot_product_attention(
        q.transpose(1, 2),
        k.transpose(1, 2),
        v.transpose(1, 2),
        attn_mask=additive_mask,
        dropout_p=0.0,
        is_causal=False,
    ).transpose(1, 2)


def _time_cuda(torch: Any, fn: Any, *, warmup: int, iters: int) -> float:
    with torch.inference_mode():
        for _ in range(warmup):
            fn()
        torch.cuda.synchronize()

        start = torch.cuda.Event(enable_timing=True)
        end = torch.cuda.Event(enable_timing=True)
        samples = []
        for _ in range(iters):
            start.record()
            fn()
            end.record()
            torch.cuda.synchronize()
            samples.append(start.elapsed_time(end))
    return statistics.median(samples)


def _profile_sparse_attention(
    torch: Any,
    fn: Any,
) -> tuple[bool, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    activities = [torch.profiler.ProfilerActivity.CPU]
    if torch.cuda.is_available():
        activities.append(torch.profiler.ProfilerActivity.CUDA)
        torch.cuda.synchronize()
    with torch.profiler.profile(activities=activities) as profiler:
        fn()
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    names = {event.key for event in profiler.key_averages()}
    dense_markers = (
        "scaled_dot_product_attention",
        "_scaled_dot_product",
        "aten::bmm",
        "aten::matmul",
        "aten::_softmax",
        "aten::softmax",
    )
    dense_events = tuple(
        sorted(name for name in names if any(marker in name for marker in dense_markers))
    )
    flashmask_cuda_kernel_events = tuple(
        sorted(
            name
            for name in names
            if any(marker in name for marker in REQUIRED_FLASHMASK_CUDA_KERNEL_MARKERS)
        )
    )
    missing_flashmask_cuda_kernel_markers = tuple(
        marker
        for marker in REQUIRED_FLASHMASK_CUDA_KERNEL_MARKERS
        if not any(marker in name for name in names)
    )
    return (
        any("flashmask::fwd" in name for name in names),
        dense_events,
        missing_flashmask_cuda_kernel_markers,
        flashmask_cuda_kernel_events,
    )


def _csv(value: str) -> list[str]:
    values = [item.strip() for item in value.split(",") if item.strip()]
    for item in values:
        if item not in DTYPE_ALIASES:
            raise ValueError(f"unsupported dtype {item!r}")
    return values


def _int_csv(value: str) -> list[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def _float_csv(value: str) -> list[float]:
    return [float(item.strip()) for item in value.split(",") if item.strip()]


def _validate_args(args: argparse.Namespace) -> None:
    dtypes = _csv(args.dtypes)
    if not dtypes:
        raise ValueError("--dtypes must contain at least one dtype")
    head_dims = _int_csv(args.head_dims)
    if not head_dims:
        raise ValueError("--head-dims must contain at least one value")
    if any(head_dim <= 0 or head_dim > 128 for head_dim in head_dims):
        raise ValueError("--head-dims values must be in [1, 128]")
    if args.batch <= 0:
        raise ValueError("--batch must be positive")
    if args.heads <= 0:
        raise ValueError("--heads must be positive")
    if args.mask_heads <= 0:
        raise ValueError("--mask-heads must be positive")
    if args.heads % args.mask_heads != 0:
        raise ValueError("--heads must be divisible by --mask-heads")
    if args.parity_seqlen < 3:
        raise ValueError("--parity-seqlen must be at least 3")
    if args.query_seqlen <= 0 or args.query_key_seqlen < 3:
        raise ValueError("--query-seqlen must be positive and --query-key-seqlen must be at least 3")
    if args.query_seqlen > args.query_key_seqlen:
        raise ValueError("--query-seqlen must be <= --query-key-seqlen")
    if args.iters <= 0:
        raise ValueError("--iters must be positive")
    if args.warmup < 0:
        raise ValueError("--warmup must be non-negative")
    if args.mode in ("all", "bench") and args.min_speedup is None:
        raise ValueError("--min-speedup is required for benchmark mode")
    if args.min_speedup is not None and args.min_speedup <= 0:
        raise ValueError("--min-speedup must be positive")
    if args.require_sm90 and args.skip_profiler_check:
        raise ValueError("--skip-profiler-check cannot be used with --require-sm90")
    pad_fractions = _float_csv(args.pad_fractions)
    if not pad_fractions:
        raise ValueError("--pad-fractions must contain at least one value")
    for pad_fraction in pad_fractions:
        if pad_fraction < 0.0 or pad_fraction >= 1.0:
            raise ValueError("--pad-fractions values must be in [0, 1)")
    bench_seq_lens = _int_csv(args.bench_seq_lens)
    if not bench_seq_lens:
        raise ValueError("--bench-seq-lens must contain at least one value")
    if any(seq_len < 3 for seq_len in bench_seq_lens):
        raise ValueError("--bench-seq-lens values must be at least 3")


def _mask_digest(mask: Any) -> str:
    payload = json.dumps(mask.to_list(), separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _jsonable(value: Any) -> Any:
    if isinstance(value, tuple):
        return list(value)
    return value


def _emit_records(
    records: list[dict[str, Any]],
    *,
    jsonl: bool,
    output_jsonl: str | None,
) -> None:
    lines = [json.dumps(record, sort_keys=True) for record in records]
    for record, line in zip(records, lines, strict=True):
        print(line if jsonl else _format_record(record))
    if output_jsonl is not None:
        output_path = Path(output_jsonl)
        if output_path.parent != Path(""):
            output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("".join(f"{line}\n" for line in lines))


def _format_record(record: dict[str, Any]) -> str:
    return " ".join(f"{key}={value}" for key, value in record.items())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
