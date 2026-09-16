# Verification Record

## Scope

This record covers the `3.1` Release-backed packaging change. Bifrost is a
CPU-only Autotools plugin. It has no optional hardware-processing backend, so
NVIDIA hardware is not relevant to its frame-processing gate.

The plugin namespace is `bifrost`; the native payload is `bifrost.dll` on
Windows, `bifrost.so` on Linux, and `bifrost.dylib` on macOS. A representative
valid invocation is `core.bifrost.Bifrost(clip, interlaced=False, blockx=4,
blocky=4)` on a constant-format 8-bit YUV clip. RGB input must fail with the
documented `Only constant format 8 bit integer YUV allowed` validation path.

## API Migration

Before and after packaging work, `migration_report.py` and `scan_api3.py`
reported zero API3 markers. The source has been API4 since upstream commit
`bec7ce0` (`Convert to API4`), with `bec7ce0^` retaining the API3 source
baseline. No local R73/API3 Python runtime or API3 binary is available, so a
separate-process API3/API4 comparison is blocked rather than inferred from the
API4 smoke tests below.

## Local Linux R79 Gate

The Python 3.13 `vpy-api4-vs79-validation` Docker image loaded the generated
Linux package with autoload disabled and an explicit `core.std.LoadPlugin`.
The source-install fallback was also run as an ordinary isolated PEP 517 build
with `BIFROST_FORCE_BUILD=1` and `PKG_CONFIG_PATH=/unrelated`; the hook found
the build environment's VapourSynth 79 headers and prepended its metadata.

The representative 64x48 `YUV420P8`, 12-frame deterministic input yielded:

| Frame | SHA-256 |
| --- | --- |
| 0 | `01807d701d24b6c65e04c6813c28490d4cef28eb1dd760b4526adf3114e017f2` |
| 3 | `22a73c28112b9bdf5a2d001296933670024e3c4c6970e7264c3b528286606a3c` |
| 11 | `aa0bed722124ba13df9a77c5ae873e3f77dfd45b7fb754015a7b7ceb888508e5` |

Frame 3 PlaneStats were Y `0.28627450980392155`, U
`0.4117647058823529`, and V `0.6196078431372549`; the invalid RGB call raised
`vapoursynth.Error`. The installed `manylinux_2_27_x86_64` wheel autoloaded
and rendered successfully. A local Release-loopback VCS-style build logged
`Bifrost wheel build: using Release asset` before the same autoload smoke.

## ABI And Release Contract

The Linux Release zip is rooted at `bifrost/` and contains exactly
`manifest.vs` and `bifrost.so` for this dependency-free plugin. The
manylinux2014/devtoolset-10 build reported a dynamically linked ELF with only
`libc.so.6`; `readelf --version-info` found `GLIBC_2.2.5` and `GLIBC_2.14`.
The published wheel is intentionally `manylinux_2_27_x86_64`: VapourSynth R79
itself sets the end-to-end Linux runtime floor even though Bifrost has lower
individual GLIBC requirements.

The `v3.1` Release is expected to contain current Windows and Linux zip,
checksum, and wheel assets. The tag workflow validates their exact inventory
and downloads them again to compare SHA-256 values. After publication, run the
documented remote VCS install in a fresh R79 container and retain the pip log
showing `using Release asset`; that gate cannot be run before the tag exists.

Windows retains its verified upstream `Bifrost-3.0.7z` payload while a matching
fork Release is unavailable, then repackages that API4 DLL as the fork's
Windows zip and wheel. Windows is not used as the source-fallback test host;
Linux and macOS exercise the actual native Autotools fallback independently.
