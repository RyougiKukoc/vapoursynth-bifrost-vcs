#!/usr/bin/env python3
"""Verify Bifrost autoload after installing a wheel."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def frame_hash(frame: Any) -> str:
    digest = hashlib.sha256()
    for plane in range(frame.format.num_planes):
        digest.update(bytes(frame[plane]))
    return digest.hexdigest()


def main() -> int:
    import vapoursynth as vs

    core = vs.core
    if getattr(core, "bifrost", None) is None:
        raise RuntimeError("Bifrost was not autoloaded from the installed wheel")
    source = core.std.Splice(
        [
            core.std.BlankClip(width=64, height=48, format=vs.YUV420P8, length=1, color=[80 + n * 2, 96, 176])
            for n in range(12)
        ]
    )
    output = core.bifrost.Bifrost(source, interlaced=False)
    frame = output.get_frame(3)
    stats = core.std.PlaneStats(output, plane=0).get_frame(3).props
    print(
        json.dumps(
            {
                "namespace_loaded": True,
                "width": frame.width,
                "height": frame.height,
                "format": frame.format.name,
                "frames": output.num_frames,
                "frame_hash": frame_hash(frame),
                "plane0_average": float(stats["PlaneStatsAverage"]),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
