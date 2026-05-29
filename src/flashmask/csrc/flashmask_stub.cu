#include <torch/extension.h>

#include <vector>

std::vector<at::Tensor> flashmask_fwd_cuda(
    const at::Tensor& q,
    const at::Tensor& k,
    const at::Tensor& v,
    const at::Tensor& startend_row_indices,
    const at::Tensor& block_mask,
    double softmax_scale,
    bool causal) {
  (void)q;
  (void)k;
  (void)v;
  (void)startend_row_indices;
  (void)block_mask;
  (void)softmax_scale;
  (void)causal;
  TORCH_CHECK(false, "FlashMask sparse FA3 forward kernel is not implemented");
}

std::vector<at::Tensor> flashmask_bwd_cuda(
    const at::Tensor& dout,
    const at::Tensor& q,
    const at::Tensor& k,
    const at::Tensor& v,
    const at::Tensor& out,
    const at::Tensor& softmax_lse,
    const at::Tensor& startend_row_indices,
    const at::Tensor& block_mask,
    double softmax_scale,
    bool causal,
    bool deterministic) {
  (void)dout;
  (void)q;
  (void)k;
  (void)v;
  (void)out;
  (void)softmax_lse;
  (void)startend_row_indices;
  (void)block_mask;
  (void)softmax_scale;
  (void)causal;
  (void)deterministic;
  TORCH_CHECK(false, "FlashMask sparse FA3 backward kernel is not implemented");
}
