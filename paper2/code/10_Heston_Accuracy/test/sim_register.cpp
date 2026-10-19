// sim_register.cpp - registers the HLS top (hq_ft or hq_qe) with xrt_sim (sandbox only)
#include <xrt/xrt_sim.h>
#include "hq_kernel.h"

#define HQ_REGISTER(top) XRT_SIM_KERNEL(top, 7)   // expands HQ_TOP before the name is stringified
HQ_REGISTER(HQ_TOP) {
    HQ_TOP(a[0].as<const float>(), a[1].as<uint64_t>(), a[2].scalar, a[3].scalar, (uint32_t)a[4].scalar,
           a[5].scalar, a[6].scalar);
}
