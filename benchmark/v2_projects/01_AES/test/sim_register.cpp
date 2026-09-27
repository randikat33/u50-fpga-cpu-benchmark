// sim_register.cpp - registers the HLS top with xrt_sim (sandbox build of the host only).
// ap_uint<512> is 64 raw little-endian bytes, so the device buffers are used in place.
#include <xrt/xrt_sim.h>
#include "aes256ctr.h"

XRT_SIM_KERNEL(aes256ctr, 5) {
    aes256ctr(a[0].as<aes_word_t>(), a[1].as<aes_word_t>(), a[2].as<aes_word_t>(),
              ap_uint<64>(a[3].scalar), aes_cnt_t(static_cast<uint32_t>(a[4].scalar)));
}
