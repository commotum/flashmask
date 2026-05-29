#include <torch/extension.h>
#include <cuda_runtime.h>

#include <vector>

#ifndef FLASHMASK_KERNEL_READY
#define FLASHMASK_KERNEL_READY 0
#endif

std::vector<at::Tensor> flashmask_fwd_cuda(
    const at::Tensor& q,
    const at::Tensor& k,
    const at::Tensor& v,
    const at::Tensor& startend_row_indices,
    const at::Tensor& block_mask,
    double softmax_scale,
    bool causal);

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
    bool deterministic);

std::vector<at::Tensor> flashmask_fwd(
    const at::Tensor& q,
    const at::Tensor& k,
    const at::Tensor& v,
    const at::Tensor& startend_row_indices,
    const at::Tensor& block_mask,
    double softmax_scale,
    bool causal) {
  TORCH_CHECK(q.is_cuda(), "q must be a CUDA tensor");
  TORCH_CHECK(k.is_cuda(), "k must be a CUDA tensor");
  TORCH_CHECK(v.is_cuda(), "v must be a CUDA tensor");
  TORCH_CHECK(startend_row_indices.is_cuda(), "startend_row_indices must be a CUDA tensor");
  TORCH_CHECK(startend_row_indices.scalar_type() == at::kInt, "startend_row_indices must be int32");
  TORCH_CHECK(q.dim() == 4, "q must have shape [B, S_q, H_q, D]");
  TORCH_CHECK(k.dim() == 4, "k must have shape [B, S_k, H_k, D]");
  TORCH_CHECK(v.dim() == 4, "v must have shape [B, S_k, H_k, D_v]");
  TORCH_CHECK(startend_row_indices.dim() == 4, "startend_row_indices must have shape [B, H_mask, S_k, bound_num]");
  return flashmask_fwd_cuda(q, k, v, startend_row_indices, block_mask, softmax_scale, causal);
}

std::vector<at::Tensor> flashmask_bwd(
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
  TORCH_CHECK(dout.is_cuda(), "dout must be a CUDA tensor");
  TORCH_CHECK(q.is_cuda(), "q must be a CUDA tensor");
  TORCH_CHECK(k.is_cuda(), "k must be a CUDA tensor");
  TORCH_CHECK(v.is_cuda(), "v must be a CUDA tensor");
  TORCH_CHECK(out.is_cuda(), "out must be a CUDA tensor");
  TORCH_CHECK(softmax_lse.is_cuda(), "softmax_lse must be a CUDA tensor");
  TORCH_CHECK(startend_row_indices.is_cuda(), "startend_row_indices must be a CUDA tensor");
  TORCH_CHECK(startend_row_indices.scalar_type() == at::kInt, "startend_row_indices must be int32");
  return flashmask_bwd_cuda(
      dout,
      q,
      k,
      v,
      out,
      softmax_lse,
      startend_row_indices,
      block_mask,
      softmax_scale,
      causal,
      deterministic);
}

TORCH_LIBRARY(flashmask, m) {
  m.def("fwd(Tensor q, Tensor k, Tensor v, Tensor startend_row_indices, Tensor block_mask, float softmax_scale, bool causal) -> Tensor[]");
  m.def("bwd(Tensor dout, Tensor q, Tensor k, Tensor v, Tensor out, Tensor softmax_lse, Tensor startend_row_indices, Tensor block_mask, float softmax_scale, bool causal, bool deterministic) -> Tensor[]");
}

TORCH_LIBRARY_IMPL(flashmask, CUDA, m) {
  m.impl("fwd", &flashmask_fwd);
  m.impl("bwd", &flashmask_bwd);
}

PYBIND11_MODULE(TORCH_EXTENSION_NAME, m) {
  m.def("kernel_ready", []() {
#if FLASHMASK_KERNEL_READY
    int device = 0;
    if (cudaGetDevice(&device) != cudaSuccess) {
      return false;
    }
    cudaDeviceProp prop;
    if (cudaGetDeviceProperties(&prop, device) != cudaSuccess) {
      return false;
    }
    return prop.major == 9;
#else
    return false;
#endif
  });
}
