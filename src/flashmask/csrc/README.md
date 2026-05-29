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
SM90-only sparse forward path for head_dim96/head_dim128 fp16/bf16, and it requires a
matching CUTLASS include directory supplied through `CUTLASS_INCLUDE_DIR` or
`FLASHMASK_CUTLASS_INCLUDE_DIR`. SM80/86 remains disabled for this sparse path
until its forward mainloop has a FlashMask metadata implementation; attempting
to instantiate the SM80/86 sparse path is compile-gated so it cannot silently
fall back to dense attention.
