#include <torch/extension.h>
#include <ATen/Dispatch.h>
#include <ATen/cuda/CUDAContext.h>
#include <c10/cuda/CUDAGuard.h>
#include <c10/cuda/CUDAException.h>

#include <cmath>
#include <cstdlib>
#include <limits>
#include <vector>

namespace {

constexpr int kThreads = 128;

bool raw_startend_validation_enabled() {
  const char* value = std::getenv("FLASHMASK_VALIDATE_RAW_OP");
  return value != nullptr && value[0] != '\0' && !(value[0] == '0' && value[1] == '\0');
}

bool any_true_sync(const at::Tensor& value) {
  return value.any().item<bool>();
}

void validate_startend_debug(
    const at::Tensor& q,
    const at::Tensor& startend_row_indices,
    bool causal) {
  const c10::cuda::CUDAGuard device_guard(q.device());
  auto min_bound = startend_row_indices.min().item<int32_t>();
  auto max_bound = startend_row_indices.max().item<int32_t>();
  TORCH_CHECK(min_bound >= 0, "startend bounds must be non-negative");
  TORCH_CHECK(max_bound <= q.size(1), "startend bounds must be <= q sequence length");

  const int64_t bound_num = startend_row_indices.size(3);
  if (causal) {
    TORCH_CHECK(bound_num != 4, "causal FlashMask intervals support bound_num 1 or 2");
    if (bound_num == 2) {
      TORCH_CHECK(
          !any_true_sync(startend_row_indices.select(3, 1).lt(startend_row_indices.select(3, 0))),
          "causal FlashMask intervals require end >= start");
    }
    return;
  }

  TORCH_CHECK(bound_num != 1, "non-causal FlashMask intervals support bound_num 2 or 4");
  if (bound_num == 2) {
    TORCH_CHECK(
        !any_true_sync(startend_row_indices.select(3, 0).lt(startend_row_indices.select(3, 1))),
        "non-causal bound_num=2 FlashMask intervals require end >= start");
    return;
  }
  TORCH_CHECK(
      !any_true_sync(startend_row_indices.select(3, 1).lt(startend_row_indices.select(3, 0))) &&
          !any_true_sync(startend_row_indices.select(3, 3).lt(startend_row_indices.select(3, 2))),
      "non-causal bound_num=4 FlashMask intervals require interval end >= start");
}

void check_forward_inputs(
    const at::Tensor& q,
    const at::Tensor& k,
    const at::Tensor& v,
    const at::Tensor& startend_row_indices,
    const at::Tensor& block_mask,
    bool causal) {
  TORCH_CHECK(q.is_cuda(), "q must be a CUDA tensor");
  TORCH_CHECK(k.is_cuda(), "k must be a CUDA tensor");
  TORCH_CHECK(v.is_cuda(), "v must be a CUDA tensor");
  TORCH_CHECK(startend_row_indices.is_cuda(), "startend_row_indices must be a CUDA tensor");
  TORCH_CHECK(k.get_device() == q.get_device(), "k must be on the same CUDA device as q");
  TORCH_CHECK(v.get_device() == q.get_device(), "v must be on the same CUDA device as q");
  TORCH_CHECK(
      startend_row_indices.get_device() == q.get_device(),
      "startend_row_indices must be on the same CUDA device as q");
  TORCH_CHECK(q.is_contiguous(), "q must be contiguous [B, S_q, H, D]");
  TORCH_CHECK(k.is_contiguous(), "k must be contiguous [B, S_k, H_k, D]");
  TORCH_CHECK(v.is_contiguous(), "v must be contiguous [B, S_k, H_k, D_v]");
  TORCH_CHECK(startend_row_indices.is_contiguous(), "startend_row_indices must be contiguous");
  TORCH_CHECK(q.scalar_type() == k.scalar_type(), "q and k must have the same dtype");
  TORCH_CHECK(q.scalar_type() == v.scalar_type(), "q and v must have the same dtype");
  TORCH_CHECK(
      q.scalar_type() == at::kHalf || q.scalar_type() == at::kBFloat16,
      "experimental SM8x forward supports fp16 and bf16 only");
  TORCH_CHECK(q.dim() == 4, "q must have shape [B, S_q, H, D]");
  TORCH_CHECK(k.dim() == 4, "k must have shape [B, S_k, H_k, D]");
  TORCH_CHECK(v.dim() == 4, "v must have shape [B, S_k, H_k, D_v]");
  TORCH_CHECK(startend_row_indices.dim() == 4, "startend_row_indices must have shape [B, H_mask, S_k, bound_num]");
  TORCH_CHECK(startend_row_indices.scalar_type() == at::kInt, "startend_row_indices must be int32");
  TORCH_CHECK(q.size(0) > 0, "batch size must be positive");
  TORCH_CHECK(q.size(1) > 0, "q sequence length must be positive");
  TORCH_CHECK(k.size(1) > 0, "k sequence length must be positive");
  TORCH_CHECK(q.size(2) > 0, "q head count must be positive");
  TORCH_CHECK(k.size(2) > 0, "k head count must be positive");
  TORCH_CHECK(q.size(3) > 0, "q head dimension must be positive");
  TORCH_CHECK(v.size(3) > 0, "value head dimension must be positive");
  TORCH_CHECK(q.size(0) == k.size(0) && q.size(0) == v.size(0), "q, k, and v batch sizes must match");
  TORCH_CHECK(k.size(1) == v.size(1), "k and v sequence lengths must match");
  TORCH_CHECK(k.size(2) == v.size(2), "k and v head counts must match");
  TORCH_CHECK(q.size(3) == k.size(3), "q and k head dimensions must match");
  TORCH_CHECK(q.size(3) <= kThreads, "experimental SM8x forward supports head_dim <= 128 only");
  TORCH_CHECK(v.size(3) <= kThreads, "experimental SM8x forward supports value head_dim <= 128 only");
  TORCH_CHECK(q.size(2) == k.size(2), "experimental SM8x forward requires q heads == kv heads");
  TORCH_CHECK(startend_row_indices.size(0) == q.size(0), "startend batch size must match q");
  TORCH_CHECK(startend_row_indices.size(2) == k.size(1), "startend seqlen_k must match k");
  TORCH_CHECK(q.size(2) % startend_row_indices.size(1) == 0, "q heads must be divisible by mask heads");
  TORCH_CHECK(
      startend_row_indices.size(3) == 1 ||
          startend_row_indices.size(3) == 2 ||
          startend_row_indices.size(3) == 4,
      "startend bound_num must be 1, 2, or 4");
  if (block_mask.defined() && block_mask.numel() != 0) {
    TORCH_CHECK(false, "experimental SM8x forward does not support block_mask yet");
  }
  if (raw_startend_validation_enabled()) {
    validate_startend_debug(q, startend_row_indices, causal);
  }
}

__device__ bool flashmask_pair_allowed(
    const int32_t* __restrict__ startend,
    int b,
    int mask_h,
    int q_idx,
    int k_idx,
    int batch,
    int mask_heads,
    int seqlen_q,
    int seqlen_k,
    int bound_num,
    bool causal) {
  (void)batch;
  const int base = ((b * mask_heads + mask_h) * seqlen_k + k_idx) * bound_num;
  const int b0 = startend[base + 0];

  if (causal) {
    const int causal_end = max(0, k_idx - (seqlen_k - seqlen_q));
    if (q_idx < causal_end) {
      return false;
    }
    if (bound_num == 1) {
      return q_idx < b0;
    }
    const int b1 = startend[base + 1];
    return !(q_idx >= b0 && q_idx < b1);
  }

  if (bound_num == 2) {
    const int b1 = startend[base + 1];
    return q_idx < b0 && q_idx >= b1;
  }

  const int b1 = startend[base + 1];
  const int b2 = startend[base + 2];
  const int b3 = startend[base + 3];
  return !((q_idx >= b0 && q_idx < b1) || (q_idx >= b2 && q_idx < b3));
}

template <typename scalar_t>
__global__ void flashmask_sm8x_forward_kernel(
    const scalar_t* __restrict__ q,
    const scalar_t* __restrict__ k,
    const scalar_t* __restrict__ v,
    const int32_t* __restrict__ startend,
    scalar_t* __restrict__ out,
    float* __restrict__ softmax_lse,
    int batch,
    int seqlen_q,
    int seqlen_k,
    int heads,
    int mask_heads,
    int head_dim,
    int value_dim,
    int bound_num,
    float softmax_scale,
    bool causal) {
  __shared__ float reduction[kThreads];

  const int q_idx = blockIdx.x;
  const int head = blockIdx.y;
  const int b = blockIdx.z;
  const int tid = threadIdx.x;
  const int mask_h = head / (heads / mask_heads);

  const int q_base = ((b * seqlen_q + q_idx) * heads + head) * head_dim;
  const int out_base = ((b * seqlen_q + q_idx) * heads + head) * value_dim;

  float row_max = -INFINITY;
  float row_sum = 0.0f;
  float acc = 0.0f;

  for (int k_idx = 0; k_idx < seqlen_k; ++k_idx) {
    if (!flashmask_pair_allowed(
            startend,
            b,
            mask_h,
            q_idx,
            k_idx,
            batch,
            mask_heads,
            seqlen_q,
            seqlen_k,
            bound_num,
            causal)) {
      continue;
    }

    float dot = 0.0f;
    if (tid < head_dim) {
      const int k_base = ((b * seqlen_k + k_idx) * heads + head) * head_dim;
      dot = static_cast<float>(q[q_base + tid]) * static_cast<float>(k[k_base + tid]);
    }
    reduction[tid] = dot;
    __syncthreads();
    for (int stride = kThreads / 2; stride > 0; stride >>= 1) {
      if (tid < stride) {
        reduction[tid] += reduction[tid + stride];
      }
      __syncthreads();
    }
    const float score = reduction[0] * softmax_scale;
    __syncthreads();
    const float new_row_max = fmaxf(row_max, score);
    const float old_scale = row_sum == 0.0f ? 0.0f : expf(row_max - new_row_max);
    const float new_scale = expf(score - new_row_max);
    if (tid < value_dim) {
      const int v_base = ((b * seqlen_k + k_idx) * heads + head) * value_dim;
      acc = acc * old_scale + static_cast<float>(v[v_base + tid]) * new_scale;
    }
    row_sum = row_sum * old_scale + new_scale;
    row_max = new_row_max;
  }

  if (tid < value_dim) {
    out[out_base + tid] = row_sum == 0.0f ? scalar_t(0.0f) : scalar_t(acc / row_sum);
  }
  if (tid == 0) {
    softmax_lse[(b * heads + head) * seqlen_q + q_idx] =
        row_sum == 0.0f ? -INFINITY : row_max + logf(row_sum);
  }
}

}  // namespace

std::vector<at::Tensor> flashmask_fwd_cuda(
    const at::Tensor& q,
    const at::Tensor& k,
    const at::Tensor& v,
    const at::Tensor& startend_row_indices,
    const at::Tensor& block_mask,
    double softmax_scale,
    bool causal) {
  check_forward_inputs(q, k, v, startend_row_indices, block_mask, causal);

  const c10::cuda::CUDAGuard device_guard(q.device());
  cudaDeviceProp prop;
  C10_CUDA_CHECK(cudaGetDeviceProperties(&prop, q.get_device()));
  const int arch = prop.major * 10 + prop.minor;
  TORCH_CHECK(arch >= 80 && arch < 90, "experimental SM8x FlashMask forward requires compute capability 8.x");

  at::Tensor out = at::empty({q.size(0), q.size(1), q.size(2), v.size(3)}, q.options());
  at::Tensor softmax_lse = at::empty({q.size(0), q.size(2), q.size(1)}, q.options().dtype(at::kFloat));

  const float scale = std::isnan(softmax_scale)
      ? static_cast<float>(1.0 / std::sqrt(static_cast<double>(q.size(3))))
      : static_cast<float>(softmax_scale);

  const dim3 grid(q.size(1), q.size(2), q.size(0));
  const dim3 block(kThreads);
  cudaStream_t stream = at::cuda::getCurrentCUDAStream(q.get_device()).stream();

  AT_DISPATCH_FLOATING_TYPES_AND2(
      at::kHalf,
      at::kBFloat16,
      q.scalar_type(),
      "flashmask_sm8x_forward",
      [&] {
        flashmask_sm8x_forward_kernel<scalar_t><<<grid, block, 0, stream>>>(
            q.data_ptr<scalar_t>(),
            k.data_ptr<scalar_t>(),
            v.data_ptr<scalar_t>(),
            startend_row_indices.data_ptr<int32_t>(),
            out.data_ptr<scalar_t>(),
            softmax_lse.data_ptr<float>(),
            static_cast<int>(q.size(0)),
            static_cast<int>(q.size(1)),
            static_cast<int>(k.size(1)),
            static_cast<int>(q.size(2)),
            static_cast<int>(startend_row_indices.size(1)),
            static_cast<int>(q.size(3)),
            static_cast<int>(v.size(3)),
            static_cast<int>(startend_row_indices.size(3)),
            scale,
            causal);
      });
  C10_CUDA_KERNEL_LAUNCH_CHECK();
  return {out, softmax_lse};
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
  TORCH_CHECK(false, "FlashMask SM8x sparse backward kernel is not implemented");
}
