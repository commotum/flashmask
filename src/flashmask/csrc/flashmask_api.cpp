#include <torch/extension.h>
#include <cuda_runtime.h>

#include <array>
#include <mutex>
#include <vector>

namespace py = pybind11;

#ifndef FLASHMASK_KERNEL_READY
#define FLASHMASK_KERNEL_READY 0
#endif
#ifndef FLASHMASK_BACKWARD_READY
#define FLASHMASK_BACKWARD_READY 0
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
  bool cuda_available = false;
  bool supported = false;
  int major = -1;
  int minor = -1;
};

CachedDeviceSupport query_current_device_info() {
  int device = 0;
  if (cudaGetDevice(&device) != cudaSuccess) {
    return {true, false, false, -1, -1};
  }
  constexpr int kMaxCachedDevices = 64;
  if (device < 0 || device >= kMaxCachedDevices) {
    cudaDeviceProp prop;
    if (cudaGetDeviceProperties(&prop, device) != cudaSuccess) {
      return {true, false, false, -1, -1};
    }
    bool supported = false;
#if FLASHMASK_KERNEL_READY
#if defined(FLASHMASK_SM8X_KERNEL_READY)
    supported = prop.major == 8 && (prop.minor == 0 || prop.minor == 6);
#else
    supported = prop.major == 9 && prop.minor == 0;
#endif
#endif
    return {true, true, supported, prop.major, prop.minor};
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
    cached = {true, false, false, -1, -1};
    return cached;
  }
  bool supported = false;
#if FLASHMASK_KERNEL_READY
#if defined(FLASHMASK_SM8X_KERNEL_READY)
  supported = prop.major == 8 && (prop.minor == 0 || prop.minor == 6);
#else
  supported = prop.major == 9 && prop.minor == 0;
#endif
#endif
  cached = {true, true, supported, prop.major, prop.minor};
  return cached;
}

}  // namespace

bool flashmask_current_device_is_supported() {
  return query_current_device_info().supported;
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
#if FLASHMASK_BACKWARD_READY
    return flashmask_current_device_is_supported();
#else
    return false;
#endif
  });
  m.def("cuda_available", []() {
    return query_current_device_info().cuda_available;
  });
  m.def("current_compute_capability", []() -> py::object {
    auto info = query_current_device_info();
    if (!info.cuda_available) {
      return py::none();
    }
    return py::make_tuple(info.major, info.minor);
  });
  m.def("supported_compute_capabilities", []() -> py::tuple {
#if FLASHMASK_KERNEL_READY
#if defined(FLASHMASK_SM8X_KERNEL_READY)
    py::tuple capabilities(2);
    capabilities[0] = py::make_tuple(8, 0);
    capabilities[1] = py::make_tuple(8, 6);
    return capabilities;
#else
    py::tuple capabilities(1);
    capabilities[0] = py::make_tuple(9, 0);
    return capabilities;
#endif
#else
    return py::tuple();
#endif
  });
}
