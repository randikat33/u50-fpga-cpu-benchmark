// sim_register.cpp - registers the HLS top with xrt_sim (sandbox only)
#include <xrt/xrt_sim.h>
#include "lm_ladder.h"
XRT_SIM_KERNEL(lm_ladder, 9) {
    lm_ladder(a[0].as<lm_word_t>(), a[1].as<lm_word_t>(), a[2].as<lm_word_t>(), a[3].as<lm_word_t>(),
              a[4].as<lm_word_t>(), a[5].as<lm_word_t>(), (int)a[6].scalar, (int)a[7].scalar, (int)a[8].scalar);
}
