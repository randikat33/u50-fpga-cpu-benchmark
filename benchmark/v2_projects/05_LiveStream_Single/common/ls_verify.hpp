// ls_verify.hpp - output check shared by both mains (untimed, after the measurement).
// Every slot still holds its last input frame and the matching output, so each slot is
// re-resized with single-threaded cv::resize(INTER_LINEAR) and compared.
// CPU path: must be identical. FPGA path (Vitis Vision fixed-point bilinear): tolerance
// max |diff| <= 2 grey levels and PSNR >= 40 dB (C-sim measured max 1, PSNR >= 51 dB).
#pragma once
#include <opencv2/opencv.hpp>
#include "ls_pipeline.hpp"

namespace ls {
inline bool verify_slots(Resizer& rz, const Geometry& g, bool exact, bench::Report& R) {
    const int S = rz.workers() * rz.slots_per_worker();
    int saved = cv::getNumThreads();
    cv::setNumThreads(1);
    double worst_max = 0, worst_psnr = 1e9;
    bool ok = true;
    for (int s = 0; s < S; s++) {
        // In pipeline mode the decoder has already refilled in_buf(s) with a later frame, so
        // out_buf(s) belongs to a different frame.  Re-run the slot (untimed, after the
        // measurement window) so input and output are the same frame by construction.
        { StageTimes t; rz.process(s / rz.slots_per_worker(), s, g, t); }
        cv::Mat in(g.in_h, g.in_w, CV_8UC3, rz.in_buf(s));
        cv::Mat got(g.out_h, g.out_w, CV_8UC3, rz.out_buf(s));
        cv::Mat ref;
        cv::resize(in, ref, cv::Size(g.out_w, g.out_h), 0, 0, cv::INTER_LINEAR);
        cv::Mat d; cv::absdiff(got, ref, d);
        double mx; cv::minMaxLoc(d.reshape(1), nullptr, &mx);
        double psnr = mx == 0 ? 999.0 : cv::PSNR(got, ref);
        worst_max = std::max(worst_max, mx);
        worst_psnr = std::min(worst_psnr, psnr);
        ok &= exact ? (mx == 0) : (mx <= 2 && psnr >= 40.0);
    }
    cv::setNumThreads(saved);
    R.set("verify_max_absdiff", worst_max);
    R.set("verify_min_psnr_db", worst_psnr);
    R.set("verify_slots", S);
    R.set("verified", ok ? 1 : 0);
    return ok;
}
}  // namespace ls
