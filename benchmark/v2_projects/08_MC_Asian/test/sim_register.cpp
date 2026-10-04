// sim_register.cpp - registers the HLS top with xrt_sim (sandbox only)
#include <xrt/xrt_sim.h>
#include "mc_asian.h"

XRT_SIM_KERNEL(mc_asian, 8) {
    mc_asian(a[0].as<const float>(), a[1].as<uint64_t>(), a[2].scalar, a[3].scalar,
             (uint32_t)a[4].scalar, (uint32_t)a[5].scalar, a[6].scalar, a[7].scalar);
}
