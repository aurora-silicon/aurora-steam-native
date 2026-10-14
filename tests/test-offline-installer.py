#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Pinned local client inputs, using synthetic archives and no network or client execution."""
import hashlib
import importlib.util
import io
import os
import stat
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

spec = importlib.util.spec_from_file_location('steam_installer', Path(__file__).resolve().parents[1] / 'install-steam-arm64.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class OfflineInstallerTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.cache = self.base / 'cache'
        self.cache.mkdir()
        self.root = self.base / 'fixture-client'
        self.manifest = self.base / installer.MANIFEST
        header = b'\x7fELF' + b'\0' * 14 + b'\xb7\0'
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as stream:
            for name in installer.NATIVE:
                stream.writestr(name, header)
        self.archive = self.cache / 'client.zip'
        self.archive.write_bytes(archive.getvalue())
        self.write_manifest()

    def write_manifest(self, filename='client.zip', size=None, digest=None, extra=''):
        size = len(self.archive.read_bytes()) if size is None else size
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest() if digest is None else digest
        self.manifest.write_text(f'"client" {{ "file" "{filename}" "size" "{size}" "sha2" "{digest}" }}{extra}')
        self.pin = hashlib.sha256(self.manifest.read_bytes()).hexdigest()

    def offline(self, pin=None):
        with mock.patch.object(installer.urllib.request, 'urlopen', side_effect=AssertionError('network forbidden')):
            installer.install_client(self.root, self.cache, offline=True,
                                     manifest_path=self.manifest, manifest_sha256=self.pin if pin is None else pin)

    def assert_untouched(self):
        self.assertFalse(self.root.exists())
        self.assertFalse(self.root.with_name(self.root.name + '.partial').exists())

    def test_complete_pinned_cache_installs_synthetic_native_payload_without_network(self):
        original = self.archive.read_bytes()
        self.offline()
        self.assertEqual((self.root / 'package' / installer.MANIFEST).read_bytes(), self.manifest.read_bytes())
        self.assertEqual(self.archive.read_bytes(), original)
        for name in installer.NATIVE:
            self.assertTrue((self.root / name).stat().st_mode & 0o111)

    def test_extraction_uses_verified_snapshot_not_later_cache_changes(self):
        extract = installer.extract
        def replace_cache_then_extract(archive, out):
            self.archive.write_bytes(b'changed after verification')
            extract(archive, out)
        with mock.patch.object(installer, 'extract', side_effect=replace_cache_then_extract):
            self.offline()
        self.assertEqual((self.root / installer.NATIVE[0]).read_bytes()[:4], b'\x7fELF')

    def test_manifest_pin_is_independent_and_required(self):
        for pin in ['0' * 64, 'not-a-hash']:
            with self.subTest(pin=pin), self.assertRaises(SystemExit):
                self.offline(pin)
            self.assert_untouched()
        with self.assertRaises(SystemExit):
            installer.install_client(self.root, self.cache, offline=True, manifest_path=self.manifest)
        self.assert_untouched()

    def test_incomplete_cache_and_partial_files_never_fetch(self):
        self.archive.rename(self.archive.with_suffix('.zip.part'))
        with self.assertRaisesRegex(SystemExit, 'complete offline cache'):
            self.offline()
        self.assert_untouched()

    def test_size_and_hash_failures_preserve_cache_and_destination(self):
        for data in [b'x', b'x' * len(self.archive.read_bytes()), self.archive.read_bytes() + b'x']:
            with self.subTest(size=len(data)):
                self.archive.write_bytes(data)
                with self.assertRaises(SystemExit):
                    self.offline()
                self.assertEqual(self.archive.read_bytes(), data)
                self.assert_untouched()

    def test_all_archives_are_checked_before_extraction(self):
        self.write_manifest(extra=' "missing" { "file" "missing.zip" "size" "1" "sha2" "' + 'a' * 64 + '" }')
        with self.assertRaises(SystemExit):
            self.offline()
        self.assert_untouched()

    def test_symlinked_manifest_or_archive_is_refused(self):
        original = self.manifest.read_bytes()
        target = self.base / 'actual-manifest'
        target.write_bytes(original)
        self.manifest.unlink()
        self.manifest.symlink_to(target)
        with self.assertRaises(SystemExit):
            self.offline()
        self.manifest.unlink()
        self.manifest.write_bytes(original)
        target = self.base / 'actual-archive'
        self.archive.rename(target)
        self.archive.symlink_to(target)
        with self.assertRaises(SystemExit):
            self.offline()
        self.assert_untouched()

    def test_nonregular_cached_input_fails_without_waiting(self):
        self.archive.unlink()
        os.mkfifo(self.archive)
        with self.assertRaisesRegex(SystemExit, 'not a regular file'):
            self.offline()
        self.assert_untouched()

    def test_pinned_malformed_archive_entries_are_refused(self):
        for name, size, digest in [('../client.zip', 1, 'a' * 64), ('client.zip', '-1', 'a' * 64),
                                   ('client.zip', 1, 'bad'), ('client.zip', 0, 'a' * 64)]:
            with self.subTest(name=name, size=size, digest=digest):
                self.write_manifest(name, size, digest)
                with self.assertRaises(SystemExit):
                    self.offline()
                self.assert_untouched()

    def test_hash_pinned_but_malformed_zip_reports_offline_failure(self):
        self.archive.write_bytes(b'not a ZIP')
        self.write_manifest()
        with self.assertRaisesRegex(SystemExit, 'Invalid offline client archive'):
            self.offline()
        self.assertFalse(self.root.exists())
        self.assertEqual(self.archive.read_bytes(), b'not a ZIP')

    def test_escaping_relative_symlink_and_child_cannot_write_outside(self):
        out = self.base / 'partial'
        out.mkdir()
        link = zipfile.ZipInfo('link')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(self.archive, 'w') as stream:
            stream.writestr(link, '../outside')
            stream.writestr('link/written', 'must not escape')
        with self.assertRaisesRegex(SystemExit, 'symlink leaves extraction'):
            installer.extract(self.archive, out)
        self.assertFalse((self.base / 'outside').exists())
        # A link left by an earlier archive must also be checked at its parent.
        (out / 'link').symlink_to('../outside')
        with zipfile.ZipFile(self.archive, 'w') as stream:
            stream.writestr('link/written', 'must not escape')
        with self.assertRaisesRegex(SystemExit, 'path leaves extraction'):
            installer.extract(self.archive, out)
        self.assertFalse((self.base / 'outside').exists())

    def test_confined_relative_symlink_allows_later_children(self):
        out = self.base / 'partial'
        out.mkdir()
        link = zipfile.ZipInfo('nested/link')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(self.archive, 'w') as stream:
            stream.writestr('real/', '')
            stream.writestr(link, '../real')
            stream.writestr('nested/link/written', 'confined')
        installer.extract(self.archive, out)
        self.assertTrue((out / 'nested/link').is_symlink())
        self.assertEqual((out / 'real/written').read_text(), 'confined')

    def test_online_default_still_fetches_manifest_and_checks_cached_archive(self):
        with mock.patch.object(installer.urllib.request, 'urlopen', return_value=io.BytesIO(self.manifest.read_bytes())) as urlopen:
            installer.install_client(self.root, self.cache)
        urlopen.assert_called_once_with(installer.CDN + installer.MANIFEST, timeout=60)
        self.assertTrue(self.root.exists())

    def test_cli_refuses_incomplete_or_launcher_only_offline_options(self):
        for arguments in [['--manifest-sha256', self.pin], ['--offline-manifest', str(self.manifest)],
                          ['--offline-manifest', str(self.manifest), '--manifest-sha256', self.pin, '--launcher-only']]:
            with self.subTest(arguments=arguments), self.assertRaises(SystemExit) as error:
                installer.main(arguments)
            self.assertEqual(error.exception.code, 2)
        self.assert_untouched()


if __name__ == '__main__':
    unittest.main()
