from __future__ import annotations

import os
from pathlib import Path

from setuptools import setup

BUILD_STUB_ENV = "FLASHMASK_BUILD_CUDA"
BUILD_EXPERIMENTAL_ENV = "FLASHMASK_BUILD_EXPERIMENTAL_CUDA"
BUILD_EXPERIMENTAL_SM8X_ENV = "FLASHMASK_BUILD_EXPERIMENTAL_SM8X_CUDA"

ROOT = Path(__file__).parent
CSRC_DIR = ROOT / "src" / "flashmask" / "csrc"
FLASHMASK_V2_DIR = CSRC_DIR / "flashmask_v2"
FLASHMASK_V2_INSTANTIATIONS_DIR = FLASHMASK_V2_DIR / "instantiations"

STUB_DEFINES = [
    "FLASHMASK_KERNEL_READY=0",
    "FLASHMASK_BACKWARD_READY=0",
]

EXPERIMENTAL_DEFINES = [
    "FLASHMASK_KERNEL_READY=1",
    "FLASHMASK_BACKWARD_READY=0",
    "FLASHMASK_EXPERIMENTAL_BUILD=1",
    "FLASHMASK_V2_DISABLE_APPENDKV",
    "FLASHMASK_V2_DISABLE_BACKWARD",
    "FLASHMASK_V2_DISABLE_CLUSTER",
    "FLASHMASK_V2_DISABLE_FP8",
    "FLASHMASK_V2_DISABLE_HDIM64",
    "FLASHMASK_V2_DISABLE_HDIM192",
    "FLASHMASK_V2_DISABLE_HDIM256",
    "FLASHMASK_V2_DISABLE_LOCAL",
    "FLASHMASK_V2_DISABLE_PACKGQA",
    "FLASHMASK_V2_DISABLE_PAGEDKV",
    "FLASHMASK_V2_DISABLE_SM8x",
    "FLASHMASK_V2_DISABLE_SOFTCAP",
    "FLASHMASK_V2_DISABLE_SPLIT",
    "FLASHMASK_V2_DISABLE_VARLEN",
]

EXPERIMENTAL_SM8X_DEFINES = [
    "FLASHMASK_KERNEL_READY=1",
    "FLASHMASK_BACKWARD_READY=1",
    "FLASHMASK_EXPERIMENTAL_BUILD=1",
    "FLASHMASK_SM8X_KERNEL_READY=1",
    "FLASHMASK_SM8X_V2_BUILD=1",
    "FLASHMASK_V2_DISABLE_APPENDKV",
    "FLASHMASK_V2_DISABLE_BACKWARD",
    "FLASHMASK_V2_DISABLE_CLUSTER",
    "FLASHMASK_V2_DISABLE_FP8",
    "FLASHMASK_V2_DISABLE_HDIM192",
    "FLASHMASK_V2_DISABLE_HDIM256",
    "FLASHMASK_V2_DISABLE_LOCAL",
    "FLASHMASK_V2_DISABLE_PACKGQA",
    "FLASHMASK_V2_DISABLE_PAGEDKV",
    "FLASHMASK_V2_DISABLE_SOFTCAP",
    "FLASHMASK_V2_DISABLE_SPLIT",
    "FLASHMASK_V2_DISABLE_VARLEN",
]

EXPERIMENTAL_FWD_SOURCES = [
    FLASHMASK_V2_INSTANTIATIONS_DIR / "flash_fwd_hdim96_bf16_sm90.cu",
    FLASHMASK_V2_INSTANTIATIONS_DIR / "flash_fwd_hdim96_fp16_sm90.cu",
    FLASHMASK_V2_INSTANTIATIONS_DIR / "flash_fwd_hdim128_bf16_sm90.cu",
    FLASHMASK_V2_INSTANTIATIONS_DIR / "flash_fwd_hdim128_fp16_sm90.cu",
]

EXPERIMENTAL_SM8X_FWD_SOURCES = [
    FLASHMASK_V2_INSTANTIATIONS_DIR / "flash_fwd_hdim96_bf16_sm80.cu",
    FLASHMASK_V2_INSTANTIATIONS_DIR / "flash_fwd_hdim96_fp16_sm80.cu",
    FLASHMASK_V2_INSTANTIATIONS_DIR / "flash_fwd_hdim128_bf16_sm80.cu",
    FLASHMASK_V2_INSTANTIATIONS_DIR / "flash_fwd_hdim128_fp16_sm80.cu",
    FLASHMASK_V2_INSTANTIATIONS_DIR / "flash_fwd_hdim96_bf16_sm86.cu",
    FLASHMASK_V2_INSTANTIATIONS_DIR / "flash_fwd_hdim96_fp16_sm86.cu",
    FLASHMASK_V2_INSTANTIATIONS_DIR / "flash_fwd_hdim128_bf16_sm86.cu",
    FLASHMASK_V2_INSTANTIATIONS_DIR / "flash_fwd_hdim128_fp16_sm86.cu",
]


def define_flags(defines: list[str]) -> list[str]:
    return [f"-D{define}" for define in defines]


def build_mode() -> str | None:
    build_stub = os.environ.get(BUILD_STUB_ENV) == "1"
    build_experimental = os.environ.get(BUILD_EXPERIMENTAL_ENV) == "1"
    build_experimental_sm8x = os.environ.get(BUILD_EXPERIMENTAL_SM8X_ENV) == "1"
    enabled = [build_stub, build_experimental, build_experimental_sm8x]
    if sum(bool(value) for value in enabled) > 1:
        raise RuntimeError(
            "Set only one FlashMask CUDA build mode: "
            f"{BUILD_STUB_ENV}=1, {BUILD_EXPERIMENTAL_ENV}=1, or "
            f"{BUILD_EXPERIMENTAL_SM8X_ENV}=1."
        )
    if build_experimental_sm8x:
        return "experimental-sm8x"
    if build_experimental:
        return "experimental"
    if build_stub:
        return "stub"
    return None


def load_torch_extension(build_kind: str):
    try:
        from torch.utils.cpp_extension import BuildExtension, CUDAExtension
    except Exception as exc:  # pragma: no cover - exercised only in extension builds
        env_name = {
            "experimental": BUILD_EXPERIMENTAL_ENV,
            "experimental-sm8x": BUILD_EXPERIMENTAL_SM8X_ENV,
        }.get(build_kind, BUILD_STUB_ENV)
        raise RuntimeError(
            f"{env_name}=1 requires a CUDA-enabled PyTorch build environment. "
            "Use a no-build-isolation editable install from that environment."
        ) from exc

    return BuildExtension, CUDAExtension


def experimental_include_dirs() -> list[str]:
    include_dirs = [
        str(CSRC_DIR),
        str(FLASHMASK_V2_DIR),
    ]
    for candidate in (
        CSRC_DIR / "third_party" / "cutlass" / "include",
        CSRC_DIR / "third_party" / "cutlass" / "tools" / "util" / "include",
    ):
        if candidate.exists():
            include_dirs.append(str(candidate))

    for env_name in ("FLASHMASK_CUTLASS_INCLUDE_DIR", "CUTLASS_INCLUDE_DIR"):
        include_dir = os.environ.get(env_name)
        if include_dir:
            include_dirs.append(include_dir)

    cutlass_home = os.environ.get("CUTLASS_HOME")
    if cutlass_home:
        include_dirs.extend(
            [
                str(Path(cutlass_home) / "include"),
                str(Path(cutlass_home) / "tools" / "util" / "include"),
            ]
        )
    require_cutlass_headers(include_dirs)
    return include_dirs


def require_cutlass_headers(include_dirs: list[str]) -> None:
    for include_dir in include_dirs:
        path = Path(include_dir)
        if (path / "cutlass" / "cutlass.h").exists() and (
            path / "cute" / "tensor.hpp"
        ).exists():
            return
    raise RuntimeError(
        "FlashMask experimental CUDA builds require CUTLASS/CUTE headers. "
        "Set CUTLASS_HOME=/path/to/cutlass, "
        "CUTLASS_INCLUDE_DIR=/path/to/cutlass/include, or "
        "FLASHMASK_CUTLASS_INCLUDE_DIR=/path/to/cutlass/include."
    )


def stub_sources() -> list[str]:
    return [
        str(CSRC_DIR / "flashmask_api.cpp"),
        str(CSRC_DIR / "flashmask_stub.cu"),
    ]


def experimental_sources() -> list[str]:
    return [
        str(CSRC_DIR / "flashmask_api.cpp"),
        str(CSRC_DIR / "flashmask_experimental.cu"),
        str(FLASHMASK_V2_DIR / "flash_prepare_scheduler.cu"),
        *[str(source) for source in EXPERIMENTAL_FWD_SOURCES],
    ]


def experimental_sm8x_sources() -> list[str]:
    return [
        str(CSRC_DIR / "flashmask_api.cpp"),
        str(CSRC_DIR / "flashmask_experimental.cu"),
        str(FLASHMASK_V2_DIR / "flash_prepare_scheduler.cu"),
        *[str(source) for source in EXPERIMENTAL_SM8X_FWD_SOURCES],
    ]


def stub_compile_args() -> dict[str, list[str]]:
    defines = define_flags(STUB_DEFINES)
    return {
        "cxx": ["-O3", "-std=c++17", *defines],
        "nvcc": ["-O3", "-std=c++17", "--use_fast_math", *defines],
    }


def experimental_compile_args() -> dict[str, list[str]]:
    defines = define_flags(EXPERIMENTAL_DEFINES)
    return {
        "cxx": ["-O3", "-std=c++17", *defines],
        "nvcc": [
            "-O3",
            "-std=c++17",
            "--use_fast_math",
            "--expt-relaxed-constexpr",
            "--expt-extended-lambda",
            "-gencode=arch=compute_90a,code=sm_90a",
            *defines,
        ],
    }


def experimental_sm8x_compile_args() -> dict[str, list[str]]:
    defines = define_flags(EXPERIMENTAL_SM8X_DEFINES)
    return {
        "cxx": ["-O3", "-std=c++17", *defines],
        "nvcc": [
            "-O3",
            "-std=c++17",
            "--use_fast_math",
            "--expt-relaxed-constexpr",
            "--expt-extended-lambda",
            "-gencode=arch=compute_80,code=sm_80",
            "-gencode=arch=compute_86,code=sm_86",
            *defines,
        ],
    }


def extension_modules():
    mode = build_mode()
    if mode is None:
        return [], {}

    if mode == "experimental":
        sources = experimental_sources()
        extra_compile_args = experimental_compile_args()
        include_dirs = experimental_include_dirs()
    elif mode == "experimental-sm8x":
        sources = experimental_sm8x_sources()
        extra_compile_args = experimental_sm8x_compile_args()
        include_dirs = experimental_include_dirs()
    else:
        sources = stub_sources()
        extra_compile_args = stub_compile_args()
        include_dirs = []

    BuildExtension, CUDAExtension = load_torch_extension(mode)
    return [
        CUDAExtension(
            name="flashmask._C",
            sources=sources,
            extra_compile_args=extra_compile_args,
            include_dirs=include_dirs,
        )
    ], {"build_ext": BuildExtension}


ext_modules, cmdclass = extension_modules()

setup(
    ext_modules=ext_modules,
    cmdclass=cmdclass,
)
