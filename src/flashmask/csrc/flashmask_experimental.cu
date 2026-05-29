#include <torch/extension.h>
#include <ATen/cuda/CUDAContext.h>
#include <c10/cuda/CUDAGuard.h>
#include <cutlass/numeric_types.h>

#include "flashmask_v2/flash.h"

#include <cmath>
#include <cstdlib>
#include <limits>
#include <vector>

namespace {

int64_t round_up(int64_t value, int64_t multiple) {
  return ((value + multiple - 1) / multiple) * multiple;
}

int64_t flashmask_maxmin_elements(int64_t batch, int64_t mask_heads, int64_t seqlen_k) {
  constexpr int64_t kWorstCaseBlockN = 64;
  constexpr int64_t kFlashmaskBufferLength = 16 * 1024;
  const int64_t nblock_seqlen = round_up((seqlen_k + kWorstCaseBlockN - 1) / kWorstCaseBlockN, 4);
  const int64_t chunk_valid_length =
      round_up((kFlashmaskBufferLength + kWorstCaseBlockN - 1) / kWorstCaseBlockN, 4);
  const int64_t chunk_padded_length =
      round_up((kFlashmaskBufferLength + kWorstCaseBlockN - 1) / kWorstCaseBlockN, 32);
  const int64_t num_chunk = (nblock_seqlen + chunk_valid_length - 1) / chunk_valid_length;
  return 8 * batch * mask_heads * num_chunk * chunk_padded_length;
}

int rounded_dim(int64_t query_head_dim, int64_t value_head_dim) {
  return query_head_dim <= 96 && value_head_dim <= 96 ? 96 : 128;
}

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
      "experimental forward supports fp16 and bf16 only");
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
  TORCH_CHECK(startend_row_indices.size(1) > 0, "startend mask head count must be positive");
  TORCH_CHECK(q.size(0) == k.size(0) && q.size(0) == v.size(0), "q, k, and v batch sizes must match");
  TORCH_CHECK(k.size(1) == v.size(1), "k and v sequence lengths must match");
  TORCH_CHECK(k.size(2) == v.size(2), "k and v head counts must match");
  TORCH_CHECK(q.size(3) == k.size(3), "q and k head dimensions must match");
  TORCH_CHECK(q.size(3) <= 128, "experimental forward supports head_dim <= 128 only");
  TORCH_CHECK(v.size(3) <= 128, "experimental forward supports value head_dim <= 128 only");
  TORCH_CHECK(q.size(2) == k.size(2), "experimental forward requires q heads == kv heads");
  TORCH_CHECK(startend_row_indices.size(0) == q.size(0), "startend batch size must match q");
  TORCH_CHECK(startend_row_indices.size(2) == k.size(1), "startend seqlen_k must match k");
  TORCH_CHECK(q.size(2) % startend_row_indices.size(1) == 0, "q heads must be divisible by mask heads");
  TORCH_CHECK(
      startend_row_indices.size(3) == 1 ||
          startend_row_indices.size(3) == 2 ||
      startend_row_indices.size(3) == 4,
      "startend bound_num must be 1, 2, or 4");
  constexpr int64_t kMaxInt = static_cast<int64_t>(std::numeric_limits<int>::max());
  TORCH_CHECK(q.size(0) <= kMaxInt, "batch size must fit int32");
  TORCH_CHECK(q.size(1) <= kMaxInt, "q sequence length must fit int32");
  TORCH_CHECK(k.size(1) <= kMaxInt, "k sequence length must fit int32");
  TORCH_CHECK(q.size(2) <= kMaxInt, "q head count must fit int32");
  TORCH_CHECK(k.size(2) <= kMaxInt, "k head count must fit int32");
  TORCH_CHECK(q.size(3) <= kMaxInt, "q head dimension must fit int32");
  TORCH_CHECK(v.size(3) <= kMaxInt, "value head dimension must fit int32");
  if (block_mask.defined() && block_mask.numel() != 0) {
    TORCH_CHECK(block_mask.is_cuda(), "block_mask must be a CUDA tensor");
    TORCH_CHECK(block_mask.get_device() == q.get_device(), "block_mask must be on the same CUDA device as q");
    TORCH_CHECK(block_mask.scalar_type() == at::kInt, "block_mask must be int32");
    TORCH_CHECK(block_mask.is_contiguous(), "block_mask must be contiguous");
    TORCH_CHECK(false, "experimental forward does not support block_mask yet");
  }
  if (raw_startend_validation_enabled()) {
    validate_startend_debug(q, startend_row_indices, causal);
  }
}

void set_startend_ptrs(
    Flash_fwd_params& params,
    const at::Tensor& startend_row_indices,
    bool causal,
    std::vector<at::Tensor>& owned_bounds) {
  const int64_t bound_num = startend_row_indices.size(3);
  owned_bounds.reserve(4);
  owned_bounds.push_back(startend_row_indices.select(3, 0).contiguous());
  params.lt_start_ptr = owned_bounds.back().data_ptr<int32_t>();

  if (causal) {
    if (bound_num == 2) {
      owned_bounds.push_back(startend_row_indices.select(3, 1).contiguous());
      params.lt_end_ptr = owned_bounds.back().data_ptr<int32_t>();
    }
    TORCH_CHECK(bound_num != 4, "causal FlashMask intervals support bound_num 1 or 2");
    return;
  }

  if (bound_num == 2) {
    owned_bounds.push_back(startend_row_indices.select(3, 1).contiguous());
    params.ut_end_ptr = owned_bounds.back().data_ptr<int32_t>();
    return;
  }

  TORCH_CHECK(bound_num == 4, "non-causal FlashMask intervals support bound_num 2 or 4");
  owned_bounds.push_back(startend_row_indices.select(3, 1).contiguous());
  params.lt_end_ptr = owned_bounds.back().data_ptr<int32_t>();
  owned_bounds.push_back(startend_row_indices.select(3, 2).contiguous());
  params.ut_start_ptr = owned_bounds.back().data_ptr<int32_t>();
  owned_bounds.push_back(startend_row_indices.select(3, 3).contiguous());
  params.ut_end_ptr = owned_bounds.back().data_ptr<int32_t>();
}

Flash_fwd_params make_forward_params(
    const at::Tensor& q,
    const at::Tensor& k,
    const at::Tensor& v,
    const at::Tensor& out,
    const at::Tensor& softmax_lse,
    const at::Tensor& flashmask_maxmin,
    const at::Tensor& startend_row_indices,
    double softmax_scale,
    bool causal,
    int arch,
    int num_sm,
    std::vector<at::Tensor>& owned_bounds) {
  Flash_fwd_params params{};
  params.q_ptr = const_cast<void*>(q.const_data_ptr());
  params.k_ptr = const_cast<void*>(k.const_data_ptr());
  params.v_ptr = const_cast<void*>(v.const_data_ptr());
  params.q_batch_stride = q.stride(0);
  params.k_batch_stride = k.stride(0);
  params.v_batch_stride = v.stride(0);
  params.q_row_stride = q.stride(1);
  params.k_row_stride = k.stride(1);
  params.v_row_stride = v.stride(1);
  params.q_head_stride = q.stride(2);
  params.k_head_stride = k.stride(2);
  params.v_head_stride = v.stride(2);
  params.v_dim_stride = v.stride(3);
  params.h = static_cast<int>(q.size(2));
  params.h_k = static_cast<int>(k.size(2));

  params.o_ptr = out.data_ptr();
  params.o_batch_stride = out.stride(0);
  params.o_row_stride = out.stride(1);
  params.o_head_stride = out.stride(2);
  params.softmax_lse_ptr = softmax_lse.data_ptr<float>();

  params.b = static_cast<int>(q.size(0));
  params.b_k = static_cast<int>(k.size(0));
  params.seqlen_q = static_cast<int>(q.size(1));
  params.seqlen_k = static_cast<int>(k.size(1));
  params.seqlen_knew = 0;
  params.d = static_cast<int>(q.size(3));
  params.dv = static_cast<int>(v.size(3));
  const int rounded_head_dim = rounded_dim(q.size(3), v.size(3));
  params.d_rounded = rounded_head_dim;
  params.dv_rounded = rounded_head_dim;
  params.seqlen_q_rounded = static_cast<int>(round_up(params.seqlen_q, 128));
  params.seqlen_k_rounded = static_cast<int>(round_up(params.seqlen_k, 128));
  params.total_q = params.b * params.seqlen_q;
  params.total_k = params.b * params.seqlen_k;
  params.total_knew = 0;

  params.scale_softmax = std::isnan(softmax_scale)
      ? static_cast<float>(1.0 / std::sqrt(static_cast<double>(params.d)))
      : static_cast<float>(softmax_scale);
  params.softcap = 0.0f;
  params.p_dropout = 1.0f;
  params.rp_dropout = 1.0f;
  params.p_dropout_in_uint8_t = 255;

  params.is_bf16 = q.scalar_type() == at::kBFloat16;
  params.is_fp32 = false;
  params.is_e4m3 = false;
  params.is_causal = causal;
  params.is_local = false;
  params.is_rotary_interleaved = false;
  params.window_size_left = -1;
  params.window_size_right = -1;
  params.num_splits = 1;
  params.pack_gqa = false;
  params.skip_scheduler_metadata_computation = false;
  params.arch = arch;
  params.num_sm = num_sm;

  params.h_flashmask = static_cast<int>(startend_row_indices.size(1));
  params.h_h_flashmask_ratio = params.h / params.h_flashmask;
  params.flashmask_maxmin_ptr = flashmask_maxmin.data_ptr<int32_t>();
  params.m_block_dim = 128;
  params.n_block_dim = 128;
  params.rank = 0;
  params.nranks = 1;
  set_startend_ptrs(params, startend_row_indices, causal, owned_bounds);
  return params;
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
  TORCH_CHECK(arch == 90, "experimental FlashMask forward requires an SM90 GPU");
  int num_sm = 0;
  C10_CUDA_CHECK(cudaDeviceGetAttribute(&num_sm, cudaDevAttrMultiProcessorCount, q.get_device()));

  at::Tensor out = at::empty({q.size(0), q.size(1), q.size(2), v.size(3)}, q.options());
  at::Tensor softmax_lse = at::empty({q.size(0), q.size(2), q.size(1)}, q.options().dtype(at::kFloat));
  at::Tensor flashmask_maxmin = at::empty(
      {flashmask_maxmin_elements(q.size(0), startend_row_indices.size(1), k.size(1))},
      startend_row_indices.options());

  std::vector<at::Tensor> owned_bounds;
  Flash_fwd_params params = make_forward_params(
      q,
      k,
      v,
      out,
      softmax_lse,
      flashmask_maxmin,
      startend_row_indices,
      softmax_scale,
      causal,
      arch,
      num_sm,
      owned_bounds);

  cudaStream_t stream = at::cuda::getCurrentCUDAStream(q.get_device()).stream();
  const int rounded_head_dim = rounded_dim(q.size(3), v.size(3));
  if (q.scalar_type() == at::kBFloat16) {
    if (rounded_head_dim == 96) {
      run_mha_fwd_<90, cutlass::bfloat16_t, 96, 96, false, false, false, false>(params, stream);
    } else {
      run_mha_fwd_<90, cutlass::bfloat16_t, 128, 128, false, false, false, false>(params, stream);
    }
  } else {
    if (rounded_head_dim == 96) {
      run_mha_fwd_<90, cutlass::half_t, 96, 96, false, false, false, false>(params, stream);
    } else {
      run_mha_fwd_<90, cutlass::half_t, 128, 128, false, false, false, false>(params, stream);
    }
  }
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
  TORCH_CHECK(false, "FlashMask sparse FA3 backward kernel is not implemented");
}
