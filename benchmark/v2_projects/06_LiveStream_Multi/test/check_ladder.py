#!/usr/bin/env python3
"""check_ladder.py VIDEO DIR TOL N
DIR holds 240p.raw ... 1080p.raw (centre-cropped encoder sizes). Each must contain, in order,
the first N frames of VIDEO resized with cv2.resize(INTER_LINEAR) to the kernel size and
centre-cropped to the encoder size (max |diff| <= TOL), each frame closer to its own reference
than to its neighbours (order check)."""
import sys
import numpy as np
import cv2

RUNGS = [("240p", 432, 240, 426), ("360p", 640, 360, 640), ("480p", 856, 480, 854),
         ("720p", 1280, 720, 1280), ("1080p", 1920, 1080, 1920)]
video, d, tol, n = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
cap = cv2.VideoCapture(video)
frames = []
for i in range(n):
    ok, f = cap.read()
    assert ok, f"video has fewer than {n} frames"
    frames.append(f)
for name, w, h, ew in RUNGS:
    data = np.fromfile(f"{d}/{name}.raw", dtype=np.uint8)
    fs = ew * h * 3
    assert data.size == n * fs, f"{name}: {data.size} bytes, expected {n} x {fs}"
    x0 = (w - ew) // 2
    refs = [cv2.resize(f, (w, h), interpolation=cv2.INTER_LINEAR)[:, x0:x0 + ew].astype(np.int16) for f in frames]
    worst = 0
    for i in range(n):
        got = data[i * fs:(i + 1) * fs].reshape(h, ew, 3).astype(np.int16)
        dd = int(np.abs(got - refs[i]).max())
        worst = max(worst, dd)
        assert dd <= tol, f"{name} frame {i}: max diff {dd} > {tol}"
        own = np.abs(got - refs[i]).mean()
        for j in (i - 1, i + 1):
            if 0 <= j < n:
                assert own < np.abs(got - refs[j]).mean(), f"{name} frame {i} looks like frame {j}"
    print(f"check_ladder: {name}: {n} frames in order, max diff {worst}")
