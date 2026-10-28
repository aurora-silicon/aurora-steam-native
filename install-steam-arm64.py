#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Ryan Murray <ryan@aurorasilicon.org>
"""Install Valve's native ARM64 Linux Steam client for the current user.

Archives are fetched from Valve or an explicitly pinned offline cache, and
checked against the size and SHA-256 listed in Valve's client manifest. Nothing from Valve is
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
import tempfile
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


def offline_manifest(path, expected_sha256):
    if path is None or expected_sha256 is None or not re.fullmatch(r'[0-9a-f]{64}', expected_sha256):
        sys.exit('Offline installation requires --offline-manifest and a trusted --manifest-sha256')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                sys.exit('Offline manifest must be a regular file')
            manifest = stream.read((16 << 20) + 1)
    except OSError as error:
        sys.exit(f'Cannot read offline manifest: {error}')
    if len(manifest) > 16 << 20 or hashlib.sha256(manifest).hexdigest() != expected_sha256:
        sys.exit('Offline manifest does not match its trusted SHA-256')
    return manifest


def offline_entries(manifest):
    try:
        entries = parse_manifest(manifest.decode('utf-8'))
    except UnicodeError:
        sys.exit('Offline manifest is not UTF-8')
    files = set()
    for entry in entries:
        name = entry['file']
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]*', name) or name in files:
            sys.exit(f'Offline manifest has an unsafe or duplicate archive name: {name}')
        if not re.fullmatch(r'[0-9]{1,20}', entry.get('size', '')) or int(entry['size']) <= 0:
            sys.exit(f'{name}: offline manifest has an invalid archive size')
        if not re.fullmatch(r'[0-9a-fA-F]{64}', entry.get('sha2', '')):
            sys.exit(f'{name}: offline manifest has no valid archive SHA-256')
        entry['sha2'] = entry['sha2'].lower()
        files.add(name)
    return entries


def cached_archive(entry, downloads, snapshot):
    """Freeze and hash cached bytes before they can be used for extraction."""
    source = downloads / entry['file']
    target = snapshot / entry['file']
    try:
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as src, target.open('xb') as dst:
            if not stat.S_ISREG(os.fstat(src.fileno()).st_mode):
                sys.exit(f'{entry["file"]}: offline cache entry is not a regular file')
            digest = hashlib.sha256()
            size = 0
            while block := src.read(1 << 20):
                size += len(block)
                if size > int(entry['size']):
                    sys.exit(f'{entry["file"]}: offline archive exceeds the pinned size')
                digest.update(block)
                dst.write(block)
    except OSError as error:
        sys.exit(f'{entry["file"]}: cannot read complete offline cache: {error}')
    if size != int(entry['size']) or digest.hexdigest() != entry['sha2']:
        sys.exit(f'{entry["file"]}: offline size or SHA-256 does not match the pinned manifest')
    return target


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
    base = out.resolve()
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = PurePosixPath(info.filename.replace('\\', '/'))
            if name.is_absolute() or '..' in name.parts:
                sys.exit(f'{archive.name}: unsafe path {info.filename}')
            dst = out / name
            if not dst.parent.resolve().is_relative_to(base):
                sys.exit(f'{archive.name}: path leaves extraction directory: {info.filename}')
            mode = info.external_attr >> 16
            if info.is_dir() or stat.S_ISDIR(mode):
                if not dst.resolve().is_relative_to(base):
                    sys.exit(f'{archive.name}: directory leaves extraction root: {info.filename}')
                dst.mkdir(parents=True, exist_ok=True)
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            data = zf.read(info)
            if dst.is_symlink() or dst.exists():
                dst.unlink()
            if stat.S_ISLNK(mode):
                link = data.decode()
                if Path(link).is_absolute() or not (dst.parent / link).resolve().is_relative_to(base):
                    sys.exit(f'{archive.name}: symlink leaves extraction directory: {info.filename}')
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


def install_client(root, downloads, *, offline=False, manifest_path=None, manifest_sha256=None):
    if root.exists() or root.is_symlink():
        sys.exit(f'{root} exists; refusing to overwrite an existing Steam installation')
    if offline:
        manifest = offline_manifest(manifest_path, manifest_sha256)
        entries = offline_entries(manifest)
        with tempfile.TemporaryDirectory(prefix='aurora-steam-cache-') as directory:
            archives = [(entry, cached_archive(entry, downloads, Path(directory))) for entry in entries]
            try:
                unpack_client(root, manifest, archives)
            except (OSError, zipfile.BadZipFile, UnicodeError) as error:
                sys.exit(f'Invalid offline client archive: {error}')
    else:
        downloads.mkdir(parents=True, exist_ok=True)
        manifest = urllib.request.urlopen(CDN + MANIFEST, timeout=60).read()
        entries = parse_manifest(manifest.decode())
        unpack_client(root, manifest, ((entry, fetch(entry, downloads)) for entry in entries))


def unpack_client(root, manifest, archives):
    partial = root.with_name(root.name + '.partial')
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir(parents=True)
    for entry, archive in archives:
        print('verified', entry['package'], flush=True)
        extract(archive, partial)
    for name in NATIVE:
        path = partial / name
        head = path.read_bytes()[:20]
        if len(head) < 20 or head[:4] != b'\x7fELF' or struct.unpack_from('<H', head, 18)[0] != 183:
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
    # Aurora Play, the small game launcher, lives beside steam-native4k.
    install_file(HERE / 'bin/aurora-play', prefix / 'bin/aurora-play', 0o755)
    play = (HERE / 'share/applications/org.aurorasilicon.Play.desktop').read_text()
    play = play.replace('Exec=aurora-play', 'Exec=' + str((prefix / 'bin/aurora-play').absolute()))
    (apps / 'org.aurorasilicon.Play.desktop').write_text(play)
    install_file(HERE / 'share/icons/hicolor/scalable/apps/org.aurorasilicon.Play.svg',
                 prefix / 'share/icons/hicolor/scalable/apps/org.aurorasilicon.Play.svg', 0o644)
    if shutil.which('update-desktop-database'):
        subprocess.run(['update-desktop-database', str(apps)], check=False)


def main(argv=None):
    home = Path.home()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--root', type=Path, default=home / '.local/share/Steam-ARM64')
    parser.add_argument('--downloads', type=Path, default=home / '.cache/aurora-steam-native/downloads')
    parser.add_argument('--prefix', type=Path, default=home / '.local')
    parser.add_argument('--launcher-only', action='store_true',
                        help='keep the existing client (~/.steam/root) and only (re)install the launcher')
    parser.add_argument('--offline-manifest', type=Path, help='use only this pinned local Valve manifest and complete archive cache; never fetch')
    parser.add_argument('--manifest-sha256', help='trusted SHA-256 of the exact local manifest, supplied independently of the cache')
    args = parser.parse_args(argv)
    if args.offline_manifest is not None:
        if args.launcher_only or args.manifest_sha256 is None:
            parser.error('--offline-manifest requires --manifest-sha256 and cannot use --launcher-only')
        if not re.fullmatch(r'[0-9a-f]{64}', args.manifest_sha256):
            parser.error('--manifest-sha256 must be 64 lowercase hex digits')
    elif args.manifest_sha256 is not None:
        parser.error('--manifest-sha256 requires --offline-manifest')
    if os.geteuid() == 0:
        sys.exit('Install as the desktop user, not root')
    prefix = args.prefix.expanduser().absolute()
    if args.launcher_only:
        root = (home / '.steam/root').resolve()
        if not (root / 'steamrtarm64/steam').exists():
            sys.exit(f'no native ARM64 Steam client under {root}')
    else:
        root = args.root.expanduser().absolute()
        install_client(root, args.downloads.expanduser(), offline=args.offline_manifest is not None,
                       manifest_path=args.offline_manifest.expanduser() if args.offline_manifest else None,
                       manifest_sha256=args.manifest_sha256)
    install_launcher(root, prefix, home)
    print(f'Native ARM64 Steam in {root}; start it with {prefix}/bin/steam-native4k')


if __name__ == '__main__':
    main()
