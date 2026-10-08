#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Ryan Murray <ryan@aurorasilicon.org>
"""Install Valve's native ARM64 Linux Steam client for the current user.

Every archive is downloaded from Valve at install time and checked against the
size and SHA-256 listed in Valve's client manifest. Nothing from Valve is
redistributed by this project.
"""
import argparse
import hashlib
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

CDN = 'https://client-update.steamstatic.com/'
MANIFEST = 'steam_client_publicbeta_linuxarm64'
NATIVE = ['steamrtarm64/steam', 'steamrtarm64/steamclient.so', 'steamrtarm64/steamwebhelper']
HERE = Path(__file__).resolve().parent


def parse_manifest(text):
    entries = []
    for name, block in re.findall(r'"([^"\n]+)"\s*\{([^{}]+)\}', text):
        values = dict(re.findall(r'"([^"\n]+)"\s*"([^"\n]+)"', block))
        if 'file' in values:
            entries.append(dict(package=name, **values))
    if not entries:
        sys.exit('Valve manifest lists no archives')
    return entries


def fetch(entry, downloads):
    target = downloads / entry['file']
    if not target.exists():
        part = target.with_name(target.name + '.part')
        with urllib.request.urlopen(CDN + entry['file'], timeout=60) as src, part.open('wb') as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        part.rename(target)
    with target.open('rb') as f:
        digest = hashlib.file_digest(f, 'sha256').hexdigest()
    if target.stat().st_size != int(entry['size']) or digest != entry['sha2']:
        target.unlink()
        sys.exit(f'{entry["file"]}: size or SHA-256 does not match the Valve manifest')
    return target


def extract(archive, out):
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = PurePosixPath(info.filename.replace('\\', '/'))
            if name.is_absolute() or '..' in name.parts:
                sys.exit(f'{archive.name}: unsafe path {info.filename}')
            dst = out / name
            mode = info.external_attr >> 16
            if info.is_dir() or stat.S_ISDIR(mode):
                dst.mkdir(parents=True, exist_ok=True)
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            data = zf.read(info)
            if dst.is_symlink() or dst.exists():
                dst.unlink()
            if stat.S_ISLNK(mode):
                link = data.decode()
                if Path(link).is_absolute():
                    sys.exit(f'{archive.name}: absolute symlink {info.filename}')
                dst.symlink_to(link)
            else:
                dst.write_bytes(data)
                dst.chmod(stat.S_IMODE(mode) or 0o644)


def link_alias(alias, target):
    if alias.is_symlink() or alias.exists():
        if alias.resolve() != target.resolve():
            sys.exit(f'{alias} already points elsewhere; leaving it unchanged')
        return
    alias.symlink_to(target)


def install_file(src, dst, mode):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    dst.chmod(mode)


def install_client(root, downloads):
    if root.exists() or root.is_symlink():
        sys.exit(f'{root} exists; refusing to overwrite an existing Steam installation')
    downloads.mkdir(parents=True, exist_ok=True)
    manifest = urllib.request.urlopen(CDN + MANIFEST, timeout=60).read()
    entries = parse_manifest(manifest.decode())
    partial = root.with_name(root.name + '.partial')
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir(parents=True)
    for entry in entries:
        print('verified', entry['package'], flush=True)
        extract(fetch(entry, downloads), partial)
    for name in NATIVE:
        path = partial / name
        head = path.read_bytes()[:20]
        if head[:4] != b'\x7fELF' or struct.unpack_from('<H', head, 18)[0] != 183:
            sys.exit(f'{name} is not an AArch64 ELF executable')
        path.chmod(path.stat().st_mode | 0o111)
    (partial / 'package').mkdir(exist_ok=True)
    (partial / 'package/beta').write_text('publicbeta')
    (partial / 'package' / MANIFEST).write_bytes(manifest)
    (partial / 'logs').mkdir(exist_ok=True)
    partial.rename(root)


def install_launcher(root, prefix, home):
    steam_dir = home / '.steam'
    steam_dir.mkdir(exist_ok=True)
    for alias in ('root', 'steam'):
        link_alias(steam_dir / alias, root)
    # Steam looks for its launch wrapper through ~/.steam/sdkarm64.
    link_alias(steam_dir / 'sdkarm64', root / 'linuxarm64')
    install_file(HERE / 'bin/steam-native4k', prefix / 'bin/steam-native4k', 0o755)
    install_file(HERE / 'libexec/steam-native4k-launch-loop.sh',
                 prefix / 'libexec/steam-native4k-launch-loop.sh', 0o755)
    apps = prefix / 'share/applications'
    # Desktop sessions do not always have ~/.local/bin on PATH: use the absolute path.
    wrapper = str((prefix / 'bin/steam-native4k').absolute())
    entry = (HERE / 'share/applications/steam-native-arm64.desktop').read_text()
    entry = entry.replace('Exec=steam-native4k', 'Exec=' + wrapper).replace('TryExec=steam-native4k', 'TryExec=' + wrapper)
    apps.mkdir(parents=True, exist_ok=True)
    (apps / 'steam-native-arm64.desktop').write_text(entry)
    icon = root / 'public/steam_tray_mono.png'
    if icon.exists():
        install_file(icon, prefix / 'share/icons/hicolor/48x48/apps/steam-native-arm64.png', 0o644)
    if shutil.which('update-desktop-database'):
        subprocess.run(['update-desktop-database', str(apps)], check=False)


def main():
    home = Path.home()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--root', type=Path, default=home / '.local/share/Steam-ARM64')
    parser.add_argument('--downloads', type=Path, default=home / '.cache/aurora-steam-native/downloads')
    parser.add_argument('--prefix', type=Path, default=home / '.local')
    parser.add_argument('--launcher-only', action='store_true',
                        help='keep the existing client (~/.steam/root) and only (re)install the launcher')
    args = parser.parse_args()
    if os.geteuid() == 0:
        sys.exit('Install as the desktop user, not root')
    prefix = args.prefix.expanduser().absolute()
    if args.launcher_only:
        root = (home / '.steam/root').resolve()
        if not (root / 'steamrtarm64/steam').exists():
            sys.exit(f'no native ARM64 Steam client under {root}')
    else:
        root = args.root.expanduser().absolute()
        install_client(root, args.downloads.expanduser())
    install_launcher(root, prefix, home)
    print(f'Native ARM64 Steam in {root}; start it with {prefix}/bin/steam-native4k')


if __name__ == '__main__':
    main()
