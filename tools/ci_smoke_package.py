#!/usr/bin/env python3
"""Explicitly load a packaged Bifrost plugin with autoload disabled."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Optional, Tuple


PLUGIN_NAME = "bifrost"


def suffix() -> str:
    return ".dll" if sys.platform == "win32" else ".dylib" if sys.platform == "darwin" else ".so"


class IsolatedEnvironmentPolicy:
    def __init__(self, flags: int) -> None:
        self.api: Any = None
        self.environment: Any = None
        self.flags = flags

    def on_policy_registered(self, api: Any) -> None:
        self.api = api
        self.environment = api.create_environment(self.flags)

    def on_policy_cleared(self) -> None:
        self.api = None
        self.environment = None

    def get_current_environment(self) -> Any:
        return self.environment

    def set_environment(self, environment: Any) -> Any:
        previous = self.environment
        if environment is not None:
            self.environment = environment
        return previous

    def is_alive(self, environment: Any) -> bool:
        return environment is self.environment

    def close(self) -> None:
        if self.api is not None and self.environment is not None:
            self.api.destroy_environment(self.environment)
            self.environment = None


def install_policy(vs: Any) -> Optional[IsolatedEnvironmentPolicy]:
    if not hasattr(vs, "register_policy") or vs.has_policy():
        return None
    policy = IsolatedEnvironmentPolicy(int(vs.DISABLE_AUTO_LOADING))
    vs.register_policy(policy)
    return policy


def package_from_zip(archive: Path) -> Tuple[Path, Path]:
    temporary = Path(tempfile.mkdtemp(prefix="bifrost-smoke-"))
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(temporary)
    directories = [path for path in temporary.iterdir() if path.is_dir()]
    if len(directories) != 1 or directories[0].name != PLUGIN_NAME:
        raise RuntimeError("{} must have one top-level {}/ directory".format(archive, PLUGIN_NAME))
    return directories[0], temporary


def frame_hash(frame: Any) -> str:
    digest = hashlib.sha256()
    for plane in range(frame.format.num_planes):
        digest.update(bytes(frame[plane]))
    return digest.hexdigest()


def stats_for_frame(core: Any, clip: Any, frame: int) -> dict[str, dict[str, float]]:
    result = {}
    for plane in range(clip.format.num_planes):
        props = core.std.PlaneStats(clip, plane=plane).get_frame(frame).props
        result[str(plane)] = {
            "average": float(props["PlaneStatsAverage"]),
            "minimum": float(props["PlaneStatsMin"]),
            "maximum": float(props["PlaneStatsMax"]),
        }
    return result


def make_input(core: Any, vs: Any) -> Any:
    clips = []
    for number in range(12):
        clips.append(
            core.std.BlankClip(
                width=64,
                height=48,
                format=vs.YUV420P8,
                length=1,
                color=[64 + number * 3, 96 + (number % 3) * 11, 176 - (number % 4) * 9],
            )
        )
    return core.std.Splice(clips)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-zip")
    parser.add_argument("--artifact-dir")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if bool(args.artifact_zip) == bool(args.artifact_dir):
        raise RuntimeError("provide exactly one of --artifact-zip or --artifact-dir")
    temporary = None
    if args.artifact_zip:
        package_dir, temporary = package_from_zip(Path(args.artifact_zip).resolve())
    else:
        package_dir = Path(args.artifact_dir).resolve()
    plugin = package_dir / (PLUGIN_NAME + suffix())
    manifest = package_dir / "manifest.vs"
    if not plugin.is_file() or not manifest.is_file():
        raise RuntimeError("package lacks {} or manifest.vs".format(plugin.name))

    handles = []
    if hasattr(os, "add_dll_directory"):
        handles.append(os.add_dll_directory(str(package_dir)))
    import vapoursynth as vs

    policy = install_policy(vs)
    try:
        core = vs.core
        core.std.LoadPlugin(str(plugin))
        source = make_input(core, vs)
        output = core.bifrost.Bifrost(source, interlaced=False, blockx=4, blocky=4)
        frames = {number: output.get_frame(number) for number in (0, 3, 11)}
        invalid_rejected = False
        try:
            core.bifrost.Bifrost(core.std.BlankClip(width=64, height=48, format=vs.RGB24), interlaced=False)
        except vs.Error:
            invalid_rejected = True
        if not invalid_rejected:
            raise RuntimeError("Bifrost accepted unsupported RGB input")
        middle = frames[3]
        report = {
            "plugin": str(plugin),
            "manifest": str(manifest),
            "width": middle.width,
            "height": middle.height,
            "format": middle.format.name,
            "frames": output.num_frames,
            "frame_hashes": {str(number): frame_hash(value) for number, value in frames.items()},
            "plane_stats": stats_for_frame(core, output, 3),
            "invalid_rgb_rejected": invalid_rejected,
        }
        print(json.dumps(report, indent=2, sort_keys=True) if args.json else report)
    finally:
        for handle in handles:
            handle.close()
        if policy is not None:
            policy.close()
        if temporary is not None:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
