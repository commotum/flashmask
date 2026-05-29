"""SM90 sparse-backend parity and benchmark harness."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from itertools import product
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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        torch = _import_torch()
    except RuntimeError as exc:
        return _skip(str(exc), args.require_sm90)

    gate = _backend_gate(torch)
    if gate["status"] != "ok":
        return _skip(gate["reason"], args.require_sm90, gate)

    from .attention import flashmask_attention

    common = _common_record(torch, gate)
    profile_state: dict[str, Any] = {"done": False, "has_flashmask_fwd": None}
    records = []
    if args.mode in ("all", "parity"):
        for dtype_name, head_dim in product(_csv(args.dtypes), _int_csv(args.head_dims)):
            records.append(
                _run_case(
                    torch,
                    flashmask_attention,
                    args,
                    common,
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
                    profile_state=profile_state,
                )
            )
            records.append(
                _run_case(
                    torch,
                    flashmask_attention,
                    args,
                    common,
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
                    profile_state=profile_state,
                )
            )
            records.append(
                _run_case(
                    torch,
                    flashmask_attention,
                    args,
                    common,
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
                    profile_state=profile_state,
                )
            )

    if args.mode in ("all", "bench"):
        for dtype_name, head_dim, seqlen, pad_fraction in product(
            _csv(args.dtypes),
            _int_csv(args.head_dims),
            _int_csv(args.bench_seq_lens),
            _float_csv(args.pad_fractions),
        ):
            records.append(
                _run_case(
                    torch,
                    flashmask_attention,
                    args,
                    common,
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
                    profile_state=profile_state,
                )
            )

    for record in records:
        print(json.dumps(record, sort_keys=True) if args.jsonl else _format_record(record))
    return 0


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
    if capability[0] != 9:
        return {
            "status": "skipped",
            "reason": f"SM90 CUDA device required, found {capability}",
            "capability": capability,
            "kernel_ready": ready,
        }
    if not ready or not info.available or not info.is_fa3 or not info.supports_sparse_mask:
        return {
            "status": "skipped",
            "reason": "compiled sparse FA3 backend is not ready",
            "capability": capability,
            "kernel_ready": ready,
        }
    return {
        "status": "ok",
        "capability": capability,
        "kernel_ready": ready,
        "module_path": info.module_path,
    }


def _skip(reason: str, require_sm90: bool, extra: dict[str, Any] | None = None) -> int:
    if require_sm90:
        raise RuntimeError(reason)
    record = {"status": "skipped", "reason": reason}
    if extra:
        record.update({key: _jsonable(value) for key, value in extra.items()})
    print(json.dumps(record, sort_keys=True))
    return 0


def _common_record(torch: Any, gate: dict[str, Any]) -> dict[str, Any]:
    return {
        "device": torch.cuda.get_device_name(),
        "capability": list(gate["capability"]),
        "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda,
        "_C_path": gate.get("module_path"),
        "kernel_ready": bool(gate["kernel_ready"]),
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
    profile_state: dict[str, Any],
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
    if not args.skip_profiler_check and not profile_state["done"]:
        profile_state["has_flashmask_fwd"] = _profile_has_flashmask_fwd(
            torch,
            lambda: flashmask_attention(q, k, v, mask, softmax_scale=scale).output,
        )
        profile_state["done"] = True
        if not profile_state["has_flashmask_fwd"]:
            raise RuntimeError("torch profiler did not observe flashmask::fwd")

    out_delta = (result.output.float() - expected_out).abs()
    lse_delta = (result.softmax_lse.float() - expected_lse).abs()
    out_max_abs = out_delta.max().item()
    lse_max_abs = lse_delta.max().item()
    out_max_rel = (out_delta / expected_out.abs().clamp_min(1e-6)).max().item()
    lse_max_rel = (lse_delta / expected_lse.abs().clamp_min(1e-6)).max().item()
    atol = args.atol_bf16 if dtype_name == "bfloat16" else args.atol_fp16
    rtol = args.rtol_bf16 if dtype_name == "bfloat16" else args.rtol_fp16
    passed = bool(out_max_abs <= atol or out_max_rel <= rtol)
    passed = passed and bool(lse_max_abs <= atol or lse_max_rel <= rtol)
    if not passed:
        raise RuntimeError(
            f"parity failed for {case}: out_abs={out_max_abs:.6g}, lse_abs={lse_max_abs:.6g}"
        )

    record: dict[str, Any] = {
        **common,
        "status": "ok",
        "backend": "fa3",
        "profiler_flashmask_fwd": profile_state["has_flashmask_fwd"],
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
        "warmup": args.warmup if benchmark else 0,
        "iters": args.iters if benchmark else 0,
        "passed": passed,
    }
    if benchmark:
        startend = torch.as_tensor(mask.to_list(), device=q.device, dtype=torch.int32)
        block_mask = torch.empty(0, device=q.device, dtype=torch.int32)
        flash_ms = _time_cuda(
            torch,
            lambda: torch.ops.flashmask.fwd(q, k, v, startend, block_mask, scale, mask.causal)[0],
            warmup=args.warmup,
            iters=args.iters,
        )
        dense_ms = _time_cuda(
            torch,
            lambda: _dense_sdpa(torch, q, k, v, additive_mask),
            warmup=args.warmup,
            iters=args.iters,
        )
        speedup = dense_ms / flash_ms
        record.update(
            {
                "flashmask_ms": flash_ms,
                "dense_sdpa_ms": dense_ms,
                "speedup": speedup,
            }
        )
        if args.min_speedup is not None and speedup < args.min_speedup:
            raise RuntimeError(f"speedup {speedup:.4f} is below required {args.min_speedup:.4f}")
    else:
        record.update({"flashmask_ms": None, "dense_sdpa_ms": None, "speedup": None})
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


def _profile_has_flashmask_fwd(torch: Any, fn: Any) -> bool:
    activities = [torch.profiler.ProfilerActivity.CPU]
    if torch.cuda.is_available():
        activities.append(torch.profiler.ProfilerActivity.CUDA)
    with torch.profiler.profile(activities=activities) as profiler:
        fn()
    return any("flashmask::fwd" in event.key for event in profiler.key_averages())


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


def _jsonable(value: Any) -> Any:
    if isinstance(value, tuple):
        return list(value)
    return value


def _format_record(record: dict[str, Any]) -> str:
    return " ".join(f"{key}={value}" for key, value in record.items())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
