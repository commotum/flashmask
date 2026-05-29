# FlashMask CUDA Backend

This directory contains the standalone PyTorch extension boundary for the
kernel-native backend described by "FlashMask: Efficient and Rich Mask Extension
of FlashAttention".

The intended public op surface is:

```text
flashmask::fwd(q, k, v, startend_row_indices, block_mask, softmax_scale, causal)
  -> [out, softmax_lse]

flashmask::bwd(dout, q, k, v, out, softmax_lse, startend_row_indices,
               block_mask, softmax_scale, causal, deterministic)
  -> [dq, dk, dv]
```

The current files register the final op names and provide narrow experimental
forward/backward wrappers for the verified SM86/SM8x path. Unsupported devices
and unimplemented variants fail closed. The low-level `torch.ops.flashmask.*` entry points assume callers pass
prevalidated mask metadata; use the Python `flashmask_attention` API for normal calls. Set
`FLASHMASK_VALIDATE_RAW_OP=1` when running debug builds or direct-op tests that
should synchronously check raw interval tensors before launch.

`flashmask_v2/` contains the CUDA source snapshot being ported into this
standalone extension. The SM90 / compute capability 9.0 path is a Hopper
template for the FlashAttention 3-compatible sparse forward path for
head_dim96/head_dim128 fp16/bf16. It requires a matching CUTLASS include
directory supplied through `CUTLASS_INCLUDE_DIR` or
`FLASHMASK_CUTLASS_INCLUDE_DIR`, and it must remain fail-closed until validated
on H100/H200 hardware.

The current strict runtime target is SM86/SM8x. Stock FA2
causal/window/padding masks do not express PE's per-key query-row interval
semantics, so the SM8x build reuses the vendored SM80/86 FlashAttention
mainloop with FlashMask interval metadata threaded into the kernel. It computes
FlashMask block-level max/min metadata, skips fully masked tiles in the
SM80/86 mainloop for PE's non-causal `bound_num=2` state-autoregressive mask,
and applies exact token-level interval masking for partial tiles. The older
standalone `flashmask_sm8x_experimental.cu` kernel is retained as development
reference only; the experimental SM8x build in `setup.py` uses
`flashmask_experimental.cu` plus the vendored FlashMask v2 instantiations.
The SM8x backward path saves `out` and `softmax_lse`, recomputes sparse
probabilities from Q/K and interval metadata, and accumulates Q/K/V gradients in
float before casting back to the input dtype. Deterministic backward and dropout
remain unsupported.
