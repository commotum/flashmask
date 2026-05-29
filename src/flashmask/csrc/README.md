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

The current files register the final op names and provide a narrow experimental
forward wrapper. Unsupported devices and unimplemented variants fail closed.
The low-level `torch.ops.flashmask.*` entry points assume callers pass
prevalidated mask metadata; use the Python `flashmask_attention` API for normal
calls. Set `FLASHMASK_VALIDATE_RAW_OP=1` when running debug builds or direct-op
tests that should synchronously check raw interval tensors before launch.

`flashmask_v2/` contains the CUDA source snapshot being ported into this
standalone extension. The current experimental kernel build target is a narrow
SM90 / compute capability 9.0 sparse forward path for head_dim96/head_dim128
fp16/bf16, and it requires a matching CUTLASS include directory supplied
through `CUTLASS_INCLUDE_DIR` or `FLASHMASK_CUTLASS_INCLUDE_DIR`.

SM86 support is also a required target, but it must be exact and kernel-native:
stock FA2 causal/window/padding masks do not express PE's per-key query-row
interval semantics. The current SM8x build reuses the vendored SM80/86
FlashAttention mainloop with FlashMask interval metadata threaded into the
kernel. It applies exact per-score interval masking inside the kernel for PE's
non-causal `bound_num=2` state-autoregressive mask, but it is not yet the final
tile-skipping implementation needed for the speed goal.
