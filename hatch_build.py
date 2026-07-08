from __future__ import annotations

import hashlib
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface
from packaging import tags


ROOT = Path(__file__).resolve().parent
PLUGIN_NAME = "bifrost"
DEFAULT_PREBUILT_URL = (
    "https://github.com/dubhatervapoursynth/vapoursynth-bifrost/"
    "releases/download/v3.0/Bifrost-3.0.7z"
)
DEFAULT_PREBUILT_SHA256 = "3024109d219c182e77a7b2479cccf69ed4dc81b3e5692fb65d33d3827b2ec167"


def _prebuilt_source() -> str:
    from os import environ

    return environ.get("BIFROST_PREBUILT_URL") or DEFAULT_PREBUILT_URL


def _expected_sha256() -> str:
    from os import environ

    return environ.get("BIFROST_PREBUILT_SHA256") or DEFAULT_PREBUILT_SHA256


def _dll_member() -> Path:
    if sys.platform != "win32":
        raise RuntimeError("Bifrost release-backed wheel builds are currently supported only on Windows")

    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64"}:
        return Path("x64") / "bifrost.dll"
    if machine in {"x86", "i386", "i686"}:
        return Path("x86") / "bifrost.dll"
    raise RuntimeError(f"unsupported Windows architecture for Bifrost release asset: {platform.machine()}")


def _fetch_prebuilt_archive(source: str, destination: Path) -> None:
    candidate = Path(source)
    if candidate.exists():
        shutil.copy2(candidate, destination)
        return

    request = urllib.request.Request(source, headers={"User-Agent": "vapoursynth-bifrost-build-hook"})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def _check_sha256(path: Path, expected: str) -> None:
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual.lower() != expected.lower():
        raise RuntimeError(f"{path.name} sha256 mismatch: expected {expected}, got {actual}")


def _find_7z() -> str | None:
    found = shutil.which("7z") or shutil.which("7z.exe")
    if found:
        return found
    for candidate in [
        Path(r"C:\Program Files\7-Zip\7z.exe"),
        Path(r"C:\Program Files (x86)\7-Zip\7z.exe"),
    ]:
        if candidate.exists():
            return str(candidate)
    return None


def _extract_7z(archive_path: Path, extract_dir: Path) -> None:
    try:
        import py7zr
    except ModuleNotFoundError:
        seven_zip = _find_7z()
        if seven_zip is None:
            raise RuntimeError("extracting the upstream .7z asset requires py7zr or a 7z executable") from None
        extract_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run([seven_zip, "x", str(archive_path), f"-o{extract_dir}", "-y"], check=True)
        return

    with py7zr.SevenZipFile(archive_path, mode="r") as archive:
        archive.extractall(path=extract_dir)


def _write_manifest(target_dir: Path) -> None:
    (target_dir / "manifest.vs").write_text(
        "[VapourSynth Manifest V1]\n"
        f"{PLUGIN_NAME}\n",
        encoding="ascii",
        newline="\n",
    )


def _stage_prebuilt_plugin(target_dir: Path) -> None:
    dll_member = _dll_member()
    source = _prebuilt_source()
    expected_sha256 = _expected_sha256()

    with tempfile.TemporaryDirectory(prefix="bifrost-prebuilt-") as temp_dir_text:
        temp_dir = Path(temp_dir_text)
        archive_path = temp_dir / (Path(source).name or "Bifrost-3.0.7z")
        extract_dir = temp_dir / "extract"
        _fetch_prebuilt_archive(source, archive_path)
        _check_sha256(archive_path, expected_sha256)
        _extract_7z(archive_path, extract_dir)

        plugin_dll = extract_dir / dll_member
        if not plugin_dll.exists():
            raise FileNotFoundError(f"prebuilt archive did not contain {dll_member.as_posix()}")

        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(plugin_dll, target_dir / f"{PLUGIN_NAME}.dll")
        _write_manifest(target_dir)

    print(f"Bifrost wheel build: using upstream release asset {source}")


class CustomHook(BuildHookInterface[Any]):
    dist_dir = ROOT / "vapoursynth" / "plugins" / PLUGIN_NAME

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        del version
        build_data["pure_python"] = False
        build_data["tag"] = f"py3-none-{next(tags.platform_tags())}"

        shutil.rmtree(self.dist_dir.parent.parent, ignore_errors=True)
        _stage_prebuilt_plugin(self.dist_dir)

    def finalize(self, version: str, build_data: dict[str, Any], artifact_path: str) -> None:
        del version, build_data, artifact_path
        shutil.rmtree(self.dist_dir.parent.parent, ignore_errors=True)
