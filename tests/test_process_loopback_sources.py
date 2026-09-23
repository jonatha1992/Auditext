"""Audio source discovery for per-app capture (no real audio devices touched).

`list_audio_apps` feeds the "Actualizar" button of the interview source list.
It used to enumerate sessions on the DEFAULT output device only, so an app
playing through any other output (Bluetooth headphones, a second speaker) was
invisible no matter how often the user pressed Actualizar. Measured on
2026-09-23: Spotify, Telegram and msedgewebview2 were all on "Auriculares
(JBL Go 3)" while the default was "Altavoces (JBL Quantum910)" - none listed.
"""

from __future__ import annotations

import unittest
from unittest import mock

# Import order matters and mirrors the app: interview_frame imports soundcard
# before process_loopback is loaded lazily. process_loopback forces comtypes into
# MTA on import; if it runs first, soundcard's own CoInitializeEx gets S_FALSE,
# which it treats as a failure (RuntimeError 0x100000001) and every later test
# module that imports soundcard dies at collection.
import soundcard  # noqa: F401

from infrastructure.audio import process_loopback


class _Ctl2:
    def __init__(self, pid: int):
        self._pid = pid

    def GetProcessId(self):  # noqa: N802 - COM name
        return self._pid


class _Ctl:
    def __init__(self, pid: int):
        self._pid = pid

    def QueryInterface(self, _iface):  # noqa: N802 - COM name
        return _Ctl2(self._pid)


class _Enumerator:
    def __init__(self, pids: list[int]):
        self._pids = pids

    def GetCount(self):  # noqa: N802 - COM name
        return len(self._pids)

    def GetSession(self, i):  # noqa: N802 - COM name
        return _Ctl(self._pids[i])


class _Manager:
    def __init__(self, pids: list[int]):
        self._pids = pids

    def GetSessionEnumerator(self):  # noqa: N802 - COM name
        return _Enumerator(self._pids)


class _Device:
    def __init__(self, name: str, pids: list[int], broken: bool = False):
        self.FriendlyName = name
        self._pids = pids
        self._broken = broken

    @property
    def AudioSessionManager(self):  # noqa: N802 - pycaw name
        if self._broken:
            raise OSError("device vanished mid-enumeration")
        return _Manager(self._pids)


NAMES = {
    7400: "chrome.exe",
    17972: "Spotify.exe",
    26380: "Telegram.exe",
    26068: "python.exe",
}


def _names(pid: int) -> str | None:
    return NAMES.get(pid)


class ListAudioAppsAcrossDevicesTests(unittest.TestCase):
    def _run(self, devices):
        with (
            mock.patch.object(process_loopback, "_render_devices", return_value=devices),
            mock.patch.object(process_loopback, "_process_name", side_effect=_names),
        ):
            return process_loopback.list_audio_apps()

    def test_app_on_a_non_default_output_is_listed(self):
        apps = self._run(
            [
                _Device("Altavoces (JBL Quantum910)", [7400, 26068]),
                _Device("Auriculares (JBL Go 3)", [17972, 26380]),
            ]
        )
        names = {name for name, _pid in apps}
        self.assertIn("Spotify.exe", names)
        self.assertIn("Telegram.exe", names)
        self.assertIn("chrome.exe", names)

    def test_a_process_on_two_outputs_is_listed_once(self):
        apps = self._run(
            [
                _Device("A", [7400]),
                _Device("B", [7400, 17972]),
            ]
        )
        self.assertEqual([pid for _n, pid in apps].count(7400), 1)

    def test_one_broken_device_does_not_hide_the_others(self):
        apps = self._run(
            [
                _Device("gone", [], broken=True),
                _Device("Auriculares (JBL Go 3)", [17972]),
            ]
        )
        self.assertEqual(apps, [("Spotify.exe", 17972)])

    def test_system_sessions_and_dead_processes_are_skipped(self):
        apps = self._run([_Device("A", [0, 999999, 7400])])
        self.assertEqual(apps, [("chrome.exe", 7400)])

    def test_result_is_sorted_by_name_case_insensitively(self):
        apps = self._run([_Device("A", [26380, 7400, 17972])])
        self.assertEqual(
            [name for name, _pid in apps],
            ["chrome.exe", "Spotify.exe", "Telegram.exe"],
        )

    def test_enumeration_failure_returns_empty_list_for_the_ui(self):
        with mock.patch.object(
            process_loopback, "_render_devices", side_effect=OSError("no COM")
        ):
            self.assertEqual(process_loopback.list_audio_apps(), [])


if __name__ == "__main__":
    unittest.main()
