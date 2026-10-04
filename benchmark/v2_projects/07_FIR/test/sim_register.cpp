// sim_register.cpp - registers the HLS top with xrt_sim (sandbox build of the host only).
#include <xrt/xrt_sim.h>
#include "fir_v2.h"

XRT_SIM_KERNEL(fir_v2, 4) {
    fir_v2(a[0].as<fir_word_t>(), a[1].as<fir_word_t>(), a[2].as<fir_word_t>(),
           fir_cnt_t(static_cast<uint32_t>(a[3].scalar)));
}
