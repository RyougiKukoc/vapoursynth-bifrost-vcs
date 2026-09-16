from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Optional, Tuple

from hatchling.builders.hooks.plugin.interface import BuildHookInterface
from packaging import tags


ROOT = Path(__file__).resolve().parent
PLUGIN_NAME = "bifrost"
REPOSITORY = "RyougiKukoc/vapoursynth-bifrost-vcs"
LINUX_PREBUILT_ASSET = "bifrost-linux-x86_64.zip"
WINDOWS_PREBUILT_ASSET = "bifrost-windows-x86_64.zip"
UPSTREAM_WINDOWS_URL = (
    "https://github.com/dubhatervapoursynth/vapoursynth-bifrost/"
    "releases/download/v3.0/Bifrost-3.0.7z"
)
UPSTREAM_WINDOWS_SHA256 = "3024109d219c182e77a7b2479cccf69ed4dc81b3e5692fb65d33d3827b2ec167"


def _truthy(value: Optional[str]) -> bool:
    return value is not None and value.lower() not in {"", "0", "false", "no", "off"}


def _project_version() -> str:
    override = os.environ.get("BIFROST_PREBUILT_VERSION")
    if override:
        return override
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    project = re.search(r"(?ms)^\[project\]\s*$(.*?)(?=^\[|\Z)", text)
    if project is None:
        raise RuntimeError("[project] section missing from pyproject.toml")
    version = re.search(r'^version\s*=\s*"([^"]+)"\s*$', project.group(1), re.MULTILINE)
    if version is None:
        raise RuntimeError("project.version missing from pyproject.toml")
    return version.group(1)


def _machine_is_x86_64() -> bool:
    return platform.machine().lower() in {"amd64", "x86_64"}


def _native_suffix() -> str:
    if sys.platform == "win32":
        return ".dll"
    if sys.platform == "darwin":
        return ".dylib"
    return ".so"


def _release_asset_name() -> Optional[str]:
    if not _machine_is_x86_64():
        return None
    if sys.platform == "win32":
        return WINDOWS_PREBUILT_ASSET
    if sys.platform == "linux":
        return LINUX_PREBUILT_ASSET
    return None


def _default_release_url(version: str) -> str:
    asset = _release_asset_name()
    if asset is None:
        raise RuntimeError("no Bifrost Release asset is published for this platform")
    tag = os.environ.get("BIFROST_PREBUILT_TAG") or "v{}".format(version)
    return "https://github.com/{}/releases/download/{}/{}".format(REPOSITORY, tag, asset)


def _prebuilt_source(version: str) -> Tuple[Optional[str], bool]:
    explicit = os.environ.get("BIFROST_PREBUILT_URL")
    if explicit:
        return explicit, True
    if _release_asset_name() is None:
        return None, False
    return _default_release_url(version), False


def _fetch(source: str, destination: Path) -> None:
    candidate = Path(source)
    if candidate.exists():
        shutil.copy2(candidate, destination)
        return
    request = urllib.request.Request(source, headers={"User-Agent": "vapoursynth-bifrost-build-hook"})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expected_sha256(source: str, explicit: bool, work_dir: Path) -> str:
    configured = os.environ.get("BIFROST_PREBUILT_SHA256")
    if configured:
        return configured.strip().split()[0]
    if explicit:
        raise RuntimeError("BIFROST_PREBUILT_URL requires BIFROST_PREBUILT_SHA256")
    checksum = work_dir / "asset.sha256"
    _fetch(source + ".sha256", checksum)
    fields = checksum.read_text(encoding="ascii").strip().split()
    if not fields or not re.fullmatch(r"[0-9a-fA-F]{64}", fields[0]):
        raise RuntimeError("Release checksum asset does not start with a SHA-256 digest")
    return fields[0]


def _write_manifest(target_dir: Path) -> None:
    (target_dir / "manifest.vs").write_text(
        "[VapourSynth Manifest V1]\n{}\n".format(PLUGIN_NAME), encoding="ascii", newline="\n"
    )


def _copy_release_zip(archive: Path, target_dir: Path) -> None:
    prefix = PLUGIN_NAME + "/"
    with zipfile.ZipFile(archive) as bundle:
        members = []
        for info in bundle.infolist():
            name = info.filename.replace("\\", "/")
            if info.is_dir():
                continue
            if not name.startswith(prefix) or name.startswith("/") or ".." in Path(name).parts:
                raise RuntimeError("Release zip must contain only a top-level {}/ directory".format(PLUGIN_NAME))
            relative = name[len(prefix) :]
            relative_path = Path(relative)
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise RuntimeError("Release zip contains an unsafe package member")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise RuntimeError("Release zip must not contain symbolic links")
            members.append((info, relative))
        if not members:
            raise RuntimeError("Release zip does not contain the Bifrost package directory")
        for info, relative in members:
            destination = target_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)

    plugin = target_dir / (PLUGIN_NAME + _native_suffix())
    manifest = target_dir / "manifest.vs"
    if not plugin.is_file() or not manifest.is_file():
        raise RuntimeError("Release zip must provide {} and manifest.vs".format(plugin.name))


def _stage_release_plugin(version: str, target_dir: Path) -> bool:
    if _truthy(os.environ.get("BIFROST_FORCE_BUILD")):
        print("Bifrost wheel build: skipping Release asset because BIFROST_FORCE_BUILD is set")
        return False
    source, explicit = _prebuilt_source(version)
    if source is None:
        print("Bifrost wheel build: no matching Release asset for this platform; using native build")
        return False

    try:
        with tempfile.TemporaryDirectory(prefix="bifrost-prebuilt-") as temp_text:
            temp_dir = Path(temp_text)
            archive = temp_dir / "bifrost-release.zip"
            _fetch(source, archive)
            expected = _expected_sha256(source, explicit, temp_dir)
            actual = _sha256(archive)
            if actual.lower() != expected.lower():
                raise RuntimeError("Release asset SHA-256 mismatch: expected {}, got {}".format(expected, actual))
            _copy_release_zip(archive, target_dir)
    except Exception as exc:
        if explicit:
            raise RuntimeError("failed to use explicit Bifrost prebuilt asset {!r}".format(source)) from exc
        print("Bifrost wheel build: Release asset unavailable at {}; falling back ({})".format(source, exc))
        return False

    print("Bifrost wheel build: using Release asset {}".format(source))
    return True


def _find_7z() -> Optional[str]:
    found = shutil.which("7z") or shutil.which("7z.exe")
    if found:
        return found
    for candidate in (Path(r"C:\Program Files\7-Zip\7z.exe"), Path(r"C:\Program Files (x86)\7-Zip\7z.exe")):
        if candidate.exists():
            return str(candidate)
    return None


def _stage_upstream_windows_plugin(target_dir: Path) -> bool:
    """Retain the existing Windows source-install path before this fork has a tag asset."""
    if sys.platform != "win32" or _truthy(os.environ.get("BIFROST_FORCE_BUILD")):
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="bifrost-upstream-") as temp_text:
            temp_dir = Path(temp_text)
            archive = temp_dir / "Bifrost-3.0.7z"
            extract = temp_dir / "extract"
            _fetch(UPSTREAM_WINDOWS_URL, archive)
            actual = _sha256(archive)
            if actual.lower() != UPSTREAM_WINDOWS_SHA256:
                raise RuntimeError("upstream Bifrost archive SHA-256 mismatch")
            try:
                import py7zr
            except ModuleNotFoundError:
                seven_zip = _find_7z()
                if seven_zip is None:
                    raise RuntimeError("py7zr or 7-Zip is required for the upstream Windows archive") from None
                subprocess.run([seven_zip, "x", str(archive), "-o{}".format(extract), "-y"], check=True)
            else:
                with py7zr.SevenZipFile(archive, mode="r") as bundle:
                    bundle.extractall(path=extract)
            source = extract / "x64" / "bifrost.dll"
            if not source.is_file():
                raise FileNotFoundError("upstream archive did not provide x64/bifrost.dll")
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target_dir / "bifrost.dll")
            _write_manifest(target_dir)
    except Exception as exc:
        print("Bifrost wheel build: upstream Windows fallback unavailable ({})".format(exc))
        return False
    print("Bifrost wheel build: using verified upstream Windows release asset")
    return True


def _vapoursynth_root() -> Path:
    configured = os.environ.get("BIFROST_VAPOURSYNTH_ROOT")
    if configured:
        root = Path(configured).resolve()
    else:
        try:
            import vapoursynth
        except ImportError as exc:
            raise RuntimeError(
                "native Bifrost builds require VapourSynth headers; install VapourSynth in the PEP 517 build environment "
                "or set BIFROST_VAPOURSYNTH_ROOT to its vapoursynth package directory"
            ) from exc
        root = Path(vapoursynth.__file__).resolve().parent
    if not (root / "include").is_dir():
        raise RuntimeError("VapourSynth root {} does not contain include/".format(root))
    return root


def _configure_build_env(temp_dir: Path) -> dict[str, str]:
    root = _vapoursynth_root()
    sdk_include = temp_dir / "sdk-include" / "vapoursynth"
    shutil.copytree(root / "include", sdk_include)
    pkgconfig = temp_dir / "pkgconfig"
    pkgconfig.mkdir(parents=True, exist_ok=True)
    (pkgconfig / "vapoursynth.pc").write_text(
        "\n".join(
            [
                "prefix={}".format(root.as_posix()),
                "includedir={}".format((temp_dir / "sdk-include").as_posix()),
                "",
                "Name: vapoursynth",
                "Description: VapourSynth wheel headers for Bifrost native builds",
                "Version: 79",
                "Cflags: -I${includedir}",
                "Libs:",
                "",
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    entries = [str(pkgconfig)]
    wheel_pkgconfig = root / "pkgconfig"
    if wheel_pkgconfig.is_dir():
        entries.append(str(wheel_pkgconfig))
    if env.get("PKG_CONFIG_PATH"):
        entries.append(env["PKG_CONFIG_PATH"])
    env["PKG_CONFIG_PATH"] = os.pathsep.join(entries)
    return env


def _run(command: list[str], cwd: Path, env: dict[str, str]) -> None:
    print("Bifrost native build:", " ".join(command))
    subprocess.run(command, cwd=str(cwd), env=env, check=True)


def _find_built_plugin(source_dir: Path) -> Path:
    suffix = _native_suffix()
    candidates = []
    for stem in ("lib" + PLUGIN_NAME, PLUGIN_NAME):
        candidates.extend(source_dir.rglob(stem + suffix))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("Autotools build did not produce a Bifrost {} plugin".format(suffix))


def _stage_native_plugin(target_dir: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="bifrost-native-") as temp_text:
        temp_dir = Path(temp_text)
        source_dir = temp_dir / "source"
        shutil.copytree(
            ROOT,
            source_dir,
            ignore=shutil.ignore_patterns(".git", ".github", "build", "dist", "vapoursynth", "__pycache__"),
        )
        # Docker can bind-mount a Windows checkout whose shell script has CRLF endings.
        autogen = source_dir / "autogen.sh"
        autogen.write_bytes(autogen.read_bytes().replace(b"\r\n", b"\n"))
        env = _configure_build_env(temp_dir)
        shell = shutil.which("bash") if sys.platform == "win32" else shutil.which("sh")
        shell = shell or shutil.which("sh")
        if shell is None:
            raise RuntimeError("native Bifrost build requires a POSIX sh (MSYS2 sh on Windows)")
        if sys.platform == "win32":
            # A non-login MSYS shell launched from PowerShell can lose Autotools from PATH.
            env.setdefault("MSYSTEM", "UCRT64")
            env.setdefault("MSYS2_PATH_TYPE", "inherit")
            _run([shell, "-lc", "./autogen.sh"], source_dir, env)
            _run([shell, "-lc", "./configure"], source_dir, env)
            _run([shell, "-lc", "make"], source_dir, env)
        else:
            _run([shell, "./autogen.sh"], source_dir, env)
            _run([shell, "./configure"], source_dir, env)
            _run(["make"], source_dir, env)
        plugin = _find_built_plugin(source_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(plugin, target_dir / (PLUGIN_NAME + _native_suffix()))
        _write_manifest(target_dir)


class CustomHook(BuildHookInterface[Any]):
    dist_dir = ROOT / "vapoursynth" / "plugins" / PLUGIN_NAME

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        del version
        build_data["pure_python"] = False
        project_version = _project_version()
        shutil.rmtree(self.dist_dir.parent.parent, ignore_errors=True)
        self.dist_dir.mkdir(parents=True, exist_ok=True)

        used_release = _stage_release_plugin(project_version, self.dist_dir)
        if not used_release:
            used_release = _stage_upstream_windows_plugin(self.dist_dir)
        if not used_release:
            _stage_native_plugin(self.dist_dir)

        override = os.environ.get("BIFROST_PLATFORM_TAG")
        if override:
            platform_tag = override
        elif used_release and sys.platform == "linux" and _machine_is_x86_64():
            platform_tag = "manylinux_2_27_x86_64"
        else:
            platform_tag = str(next(tags.platform_tags()))
        build_data["tag"] = "py3-none-{}".format(platform_tag)

    def finalize(self, version: str, build_data: dict[str, Any], artifact_path: str) -> None:
        del version, build_data, artifact_path
        shutil.rmtree(self.dist_dir.parent.parent, ignore_errors=True)
