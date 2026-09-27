// sim_register.cpp - registers the HLS top with xrt_sim (sandbox only)
#include <xrt/xrt_sim.h>
#include "pf_kernel.h"
XRT_SIM_KERNEL(pf_kernel, PF_NARGS) {
    pf_kernel(a[PF_A_TRADES_F].as<float>(), a[PF_A_TRADES_I].as<uint32_t>(), a[PF_A_IDS].as<uint32_t>(),
              a[PF_A_MARKET].as<float>(), a[PF_A_OUT].as<uint64_t>(),
              (uint32_t)a[PF_A_N_TRADES].scalar, (uint32_t)a[PF_A_N_IDS].scalar, (uint32_t)a[PF_A_ID_BASE].scalar,
              (uint32_t)a[PF_A_USE_IDS].scalar, (uint32_t)a[PF_A_N_UNDER].scalar, (uint32_t)a[PF_A_N_PATHS].scalar,
              (uint32_t)a[PF_A_N_STEPS].scalar, (uint32_t)a[PF_A_CHUNK].scalar, (uint64_t)a[PF_A_SEED].scalar,
              (uint32_t)a[PF_A_STAGE].scalar, (uint32_t)a[PF_A_FLAGS].scalar);
}
