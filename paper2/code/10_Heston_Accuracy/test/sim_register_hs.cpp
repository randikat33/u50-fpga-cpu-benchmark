// sim_register_hs.cpp - registers the secondary-case HLS tops with xrt_sim (sandbox only)
#include <xrt/xrt_sim.h>
#include "hb_kernel.h"
#include "ha_kernel.h"

#define HS_REGISTER(top) XRT_SIM_KERNEL(top, 7)
HS_REGISTER(HB_TOP) {
    HB_TOP(a[0].as<const float>(), a[1].as<uint64_t>(), a[2].scalar, a[3].scalar, (uint32_t)a[4].scalar, a[5].scalar, a[6].scalar);
}
HS_REGISTER(HA_TOP) {
    HA_TOP(a[0].as<const float>(), a[1].as<uint64_t>(), a[2].scalar, a[3].scalar, (uint32_t)a[4].scalar, a[5].scalar, a[6].scalar);
}
