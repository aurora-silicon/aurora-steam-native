#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Online archive download: progress, and the IPv4 retry for slow or failed routes. No network."""
import contextlib
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

    def test_failed_ipv4_attempt_exits_and_leaves_no_partial_file(self):
        with self.opened(urllib.error.URLError('down'), urllib.error.URLError('down')), \
                self.assertRaises(SystemExit) as raised:
            self.fetch()
        self.assertIn('download failed', str(raised.exception))
        self.assertEqual(list(self.downloads.iterdir()), [])

    def test_ipv4_only_resolves_inet_and_restores_the_resolver(self):
        real = socket.getaddrinfo
        with installer.ipv4_only():
            families = {info[0] for info in socket.getaddrinfo('localhost', 80)}
        self.assertEqual(families, {socket.AF_INET})
        self.assertIs(socket.getaddrinfo, real)


if __name__ == '__main__':
    unittest.main()
