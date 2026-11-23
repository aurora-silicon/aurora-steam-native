#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Online archive download: progress, and the IPv4 retry for slow or failed routes. No network."""
import contextlib
import errno
import hashlib
import importlib.util
import io
from pathlib import Path
import socket
import tempfile
import unittest
from unittest import mock
import urllib.error

spec = importlib.util.spec_from_file_location('steam_installer', Path(__file__).resolve().parents[1] / 'install-steam-arm64.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

PAYLOAD = bytes(range(256)) * 16384  # 4 MiB


class Response(io.BytesIO):
    """A urlopen result; read1 hands out the payload in the chunks the installer asks for."""
    def __init__(self, data=PAYLOAD):
        super().__init__(data)



class DownloadTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.downloads = Path(temporary.name)
        self.entry = {'package': 'bins_test', 'file': 'bins_test.zip.0', 'size': str(len(PAYLOAD)),
                      'sha2': hashlib.sha256(PAYLOAD).hexdigest()}
        self.families = []
        installer.use_ipv4 = False
        installer.no_ipv4 = False

    def opened(self, *responses):
        """urlopen stand-in: records whether each call ran under ipv4_only, then answers in order."""
        answers = iter(responses)
        def urlopen(url, timeout):
            self.families.append(socket.getaddrinfo is not self.real_getaddrinfo)
            answer = next(answers)
            if isinstance(answer, BaseException):
                raise answer
            return answer
        self.real_getaddrinfo = socket.getaddrinfo
        return mock.patch.object(installer.urllib.request, 'urlopen', side_effect=urlopen)

    def fetch(self, entry=None):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            target = installer.fetch(entry or self.entry, self.downloads, 2, 21)
        return target, stderr.getvalue()

    def test_progress_names_the_archive_and_its_place(self):
        with self.opened(Response()):
            target, log = self.fetch()
        self.assertEqual(target.read_bytes(), PAYLOAD)
        self.assertIn('[2/21] bins_test: downloading 4 MB', log)
        self.assertIn('[2/21] bins_test: 4 of 4 MB', log)
        self.assertEqual(self.families, [False])

    def test_slow_route_is_abandoned_and_fetched_again_over_ipv4(self):
        clock = iter(range(0, 100000, 20))  # every read appears to take 20 s
        with mock.patch.object(installer.time, 'monotonic', side_effect=lambda: next(clock)), \
                self.opened(Response(), Response()):
            target, log = self.fetch()
        self.assertEqual(target.read_bytes(), PAYLOAD)
        self.assertIn('retrying over IPv4', log)
        self.assertEqual(self.families, [False, True])
        self.assertIs(socket.getaddrinfo, self.real_getaddrinfo)

    def test_failed_connection_is_retried_over_ipv4(self):
        with self.opened(urllib.error.URLError('Network is unreachable'), Response()):
            target, log = self.fetch()
        self.assertEqual(target.read_bytes(), PAYLOAD)
        self.assertEqual(self.families, [False, True])

    def test_later_archives_go_straight_to_ipv4_after_a_retry(self):
        second = dict(self.entry, package='bins_next', file='bins_next.zip.0')
        with self.opened(urllib.error.URLError('timed out'), Response(), Response()):
            self.fetch()
            target, log = self.fetch(second)
        self.assertEqual(target.read_bytes(), PAYLOAD)
        self.assertIn('downloading 4 MB over IPv4', log)
        self.assertNotIn('retrying', log)
        self.assertEqual(self.families, [False, True, True])

    def test_ipv4_attempt_is_not_held_to_the_speed_floor(self):
        clock = iter(range(0, 100000, 20))
        with mock.patch.object(installer.time, 'monotonic', side_effect=lambda: next(clock)), \
                self.opened(urllib.error.URLError('timed out'), Response()):
            target, _ = self.fetch()
        self.assertEqual(target.read_bytes(), PAYLOAD)

    def test_failed_ipv4_and_default_attempts_exit_and_leave_no_partial_file(self):
        with self.opened(urllib.error.URLError('down'), urllib.error.URLError('down'),
                         urllib.error.URLError('down')), \
                self.assertRaises(SystemExit) as raised:
            self.fetch()
        self.assertIn('download failed', str(raised.exception))
        self.assertEqual(list(self.downloads.iterdir()), [])
        self.assertEqual(self.families, [False, True, False])

    def test_ipv4_failure_falls_back_to_the_default_route_without_the_floor(self):
        # IPv6-only (NAT64) network: the default route trickles, IPv4 does not resolve.
        clock = iter(range(0, 100000, 20))
        no_ipv4 = urllib.error.URLError(socket.gaierror(socket.EAI_NONAME, 'Name or service not known'))
        with mock.patch.object(installer.time, 'monotonic', side_effect=lambda: next(clock)), \
                self.opened(Response(), no_ipv4, Response()):
            target, log = self.fetch()
        self.assertEqual(target.read_bytes(), PAYLOAD)
        self.assertIn('retrying on the default route', log)
        self.assertEqual(self.families, [False, True, False])

    def test_cut_transfer_is_retried_over_ipv4(self):
        with self.opened(Response(PAYLOAD[:1 << 20]), Response()):
            target, log = self.fetch()
        self.assertEqual(target.read_bytes(), PAYLOAD)
        self.assertIn('connection closed after 1048576 of 4194304 bytes; retrying over IPv4', log)
        self.assertEqual(self.families, [False, True])

    def test_cut_transfer_everywhere_is_a_download_failure(self):
        cut = PAYLOAD[:1 << 20]
        with self.opened(Response(cut), Response(cut), Response(cut)), \
                self.assertRaises(SystemExit) as raised:
            self.fetch()
        self.assertIn('download failed: connection closed after', str(raised.exception))
        self.assertNotIn('SHA-256', str(raised.exception))
        self.assertEqual(list(self.downloads.iterdir()), [])

    def test_ipv4_is_kept_for_later_archives_only_after_it_worked(self):
        second = dict(self.entry, package='bins_next', file='bins_next.zip.0')
        with self.opened(urllib.error.URLError('timed out'), urllib.error.URLError('no route'), Response(),
                         Response()):
            self.fetch()
            target, log = self.fetch(second)
        self.assertEqual(target.read_bytes(), PAYLOAD)
        self.assertNotIn('over IPv4', log)
        self.assertEqual(self.families, [False, True, False, False])

    def test_after_ipv4_failed_later_archives_skip_the_speed_check_and_ipv4(self):
        # IPv6-only network with a slow route: the second archive is not abandoned at
        # SLOW_AFTER and IPv4 is not tried again; it is fetched once on the default route.
        second = dict(self.entry, package='bins_next', file='bins_next.zip.0')
        clock = iter(range(0, 100000, 20))
        no_ipv4 = urllib.error.URLError(OSError(113, 'No route to host'))
        with mock.patch.object(installer.time, 'monotonic', side_effect=lambda: next(clock)), \
                self.opened(Response(), no_ipv4, Response(), Response()):
            self.fetch()
            target, log = self.fetch(second)
        self.assertEqual(target.read_bytes(), PAYLOAD)
        self.assertNotIn('retrying', log)
        self.assertEqual(self.families, [False, True, False, False])

    def test_http_error_is_not_retried_over_ipv4(self):
        not_found = urllib.error.HTTPError(installer.CDN + 'x', 404, 'Not Found', {}, None)
        with self.opened(not_found), self.assertRaises(SystemExit) as raised:
            self.fetch()
        self.assertIn('download failed', str(raised.exception))
        self.assertEqual(self.families, [False])
        self.assertFalse(installer.use_ipv4)
        self.assertEqual(list(self.downloads.iterdir()), [])

    def test_local_write_error_is_not_retried_over_ipv4(self):
        class Full(Response):
            def read1(self, n=-1):
                raise OSError(errno.ENOSPC, 'No space left on device')
        with self.opened(Full()), self.assertRaises(SystemExit) as raised:
            self.fetch()
        self.assertIn('No space left', str(raised.exception))
        self.assertEqual(self.families, [False])
        self.assertFalse(installer.use_ipv4)
        self.assertEqual(list(self.downloads.iterdir()), [])

    def test_ipv4_only_resolves_inet_and_restores_the_resolver(self):
        real = socket.getaddrinfo
        with installer.ipv4_only():
            families = {info[0] for info in socket.getaddrinfo('localhost', 80)}
        self.assertEqual(families, {socket.AF_INET})
        self.assertIs(socket.getaddrinfo, real)


if __name__ == '__main__':
    unittest.main()
