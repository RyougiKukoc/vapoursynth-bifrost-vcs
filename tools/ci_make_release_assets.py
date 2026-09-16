#!/usr/bin/env python3
"""Create release zips and checksums from a built Bifrost wheel."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "bifrost"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_dir_from_wheel(wheel: Path, destination: Path) -> Path:
    prefix = "vapoursynth/plugins/{}/".format(PLUGIN_NAME)
    with zipfile.ZipFile(wheel) as bundle:
        members = [name for name in bundle.namelist() if name.startswith(prefix) and not name.endswith("/")]
        if not members:
            raise RuntimeError("{} does not contain {}".format(wheel, prefix))
        package_dir = destination / PLUGIN_NAME
        for member in members:
            output = package_dir / member[len(prefix) :]
            output.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(member) as source, output.open("wb") as handle:
                shutil.copyfileobj(source, handle)
    return package_dir


def make_zip(package_dir: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(package_dir.rglob("*")):
            if path.is_file():
                bundle.write(path, "{}/{}".format(PLUGIN_NAME, path.relative_to(package_dir).as_posix()))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--zip-name", required=True)
    args = parser.parse_args()

    wheel_dir = (ROOT / args.wheel_dir).resolve()
    wheels = sorted(wheel_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("expected exactly one wheel under {}, found {}".format(wheel_dir, len(wheels)))
    out_dir = (ROOT / args.out_dir).resolve()
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    shutil.copy2(wheels[0], out_dir / wheels[0].name)

    with tempfile.TemporaryDirectory(prefix="bifrost-release-") as temp_text:
        package_dir = package_dir_from_wheel(wheels[0], Path(temp_text))
        suffixes = (".dll", ".so", ".dylib")
        if not (package_dir / "manifest.vs").is_file() or not any((package_dir / (PLUGIN_NAME + suffix)).is_file() for suffix in suffixes):
            raise RuntimeError("wheel package lacks Bifrost manifest or native plugin")
        archive = out_dir / args.zip_name
        make_zip(package_dir, archive)
    checksum = out_dir / (args.zip_name + ".sha256")
    checksum.write_text("{}  {}\n".format(sha256(archive), args.zip_name), encoding="ascii")
    print("zip={}".format(archive))
    print("sha256={}".format(sha256(archive)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
