#!/usr/bin/env python3
"""check_frames.py VIDEO RAW W H TOL [N]
Checks that RAW (concatenated BGR frames W x H) holds, in order, the bilinear resize of the
first N frames of VIDEO: max |diff| <= TOL against cv2.resize(INTER_LINEAR) of frame i, and
frame i is closer to reference i than to references i-1 / i+1 (order check)."""
import sys
import numpy as np
import cv2

video, raw, w, h, tol = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
data = np.fromfile(raw, dtype=np.uint8)
fs = w * h * 3
assert data.size % fs == 0, f"raw size {data.size} not a multiple of {fs}"
n = data.size // fs
if len(sys.argv) > 6:
    assert n == int(sys.argv[6]), f"expected {sys.argv[6]} frames, got {n}"
cap = cv2.VideoCapture(video)
refs = []
for i in range(n):
    ok, f = cap.read()
    assert ok, f"video has fewer than {n} frames"
    refs.append(cv2.resize(f, (w, h), interpolation=cv2.INTER_LINEAR).astype(np.int16))
worst = 0
for i in range(n):
    got = data[i * fs:(i + 1) * fs].reshape(h, w, 3).astype(np.int16)
    d = int(np.abs(got - refs[i]).max())
    worst = max(worst, d)
    assert d <= tol, f"frame {i}: max diff {d} > {tol}"
    own = np.abs(got - refs[i]).mean()
    for j in (i - 1, i + 1):
        if 0 <= j < n:
            other = np.abs(got - refs[j]).mean()
            assert own < other, f"frame {i} looks like frame {j} (order error)"
print(f"check_frames: {n} frames in order, max diff {worst} (tol {tol})")
