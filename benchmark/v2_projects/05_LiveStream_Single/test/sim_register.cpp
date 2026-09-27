// sim_register.cpp - registers the HLS top with xrt_sim (sandbox only)
#include <xrt/xrt_sim.h>
#include "ls_resize.h"
XRT_SIM_KERNEL(ls_resize, 6) {
    ls_resize(a[0].as<ls_word_t>(), a[1].as<ls_word_t>(), (int)a[2].scalar, (int)a[3].scalar, (int)a[4].scalar,
              (int)a[5].scalar);
}
