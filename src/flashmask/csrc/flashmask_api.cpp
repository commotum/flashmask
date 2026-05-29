#include <torch/extension.h>
#include <cuda_runtime.h>

#include <array>
#include <mutex>
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

namespace {

struct CachedDeviceSupport {
  bool initialized = false;
  bool supported = false;
};

CachedDeviceSupport query_current_device_support() {
#if FLASHMASK_KERNEL_READY
  int device = 0;
  if (cudaGetDevice(&device) != cudaSuccess) {
    return {};
  }
  constexpr int kMaxCachedDevices = 64;
  if (device < 0 || device >= kMaxCachedDevices) {
    cudaDeviceProp prop;
    if (cudaGetDeviceProperties(&prop, device) != cudaSuccess) {
      return {};
    }
#if defined(FLASHMASK_SM8X_KERNEL_READY)
    return {true, prop.major == 8};
#else
    return {true, prop.major == 9 && prop.minor == 0};
#endif
  }

  static std::array<CachedDeviceSupport, kMaxCachedDevices> cache{};
  static std::mutex cache_mutex;
  std::lock_guard<std::mutex> lock(cache_mutex);
  CachedDeviceSupport& cached = cache[device];
  if (cached.initialized) {
    return cached;
  }

  cudaDeviceProp prop;
  if (cudaGetDeviceProperties(&prop, device) != cudaSuccess) {
    return {};
  }
#if defined(FLASHMASK_SM8X_KERNEL_READY)
  cached = {true, prop.major == 8};
#else
  cached = {true, prop.major == 9 && prop.minor == 0};
#endif
  return cached;
#else
  return {};
#endif
}

}  // namespace

bool flashmask_current_device_is_supported() {
  return query_current_device_support().supported;
}

bool flashmask_current_device_is_sm90() {
#if defined(FLASHMASK_SM8X_KERNEL_READY)
  return false;
#else
  return flashmask_current_device_is_supported();
#endif
}

PYBIND11_MODULE(TORCH_EXTENSION_NAME, m) {
  m.def("backend_kind", []() {
#if FLASHMASK_KERNEL_READY
#if defined(FLASHMASK_SM8X_KERNEL_READY)
    return "sm8x_sparse_fa2_compatible";
#else
    return "sm90_sparse_fa3";
#endif
#else
    return "stub";
#endif
  });
  m.def("kernel_ready", []() {
    return flashmask_current_device_is_supported();
  });
  m.def("forward_ready", []() {
    return flashmask_current_device_is_supported();
  });
  m.def("backward_ready", []() {
    return false;
  });
}
