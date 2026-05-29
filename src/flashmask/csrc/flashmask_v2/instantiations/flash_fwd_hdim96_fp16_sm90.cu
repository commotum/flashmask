#include "../flash_fwd_launch_template.h"

#ifndef FLASHMASK_V2_DISABLE_HDIM96
template void run_mha_fwd_<
    90,
    cutlass::half_t,
    96,
    96,
    false,
    false,
    false,
    false>(Flash_fwd_params& params, cudaStream_t stream);
#endif
