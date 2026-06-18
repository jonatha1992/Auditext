"""Per-application audio capture via the Windows WASAPI Process Loopback API.

`soundcard` only captures at the device level (everything routed to a speaker).
To capture the audio of a single application we use the Process Loopback API
(``ActivateAudioInterfaceAsync`` with ``AUDIOCLIENT_ACTIVATION_PARAMS`` /
``PROCESS_LOOPBACK``), available on Windows 10 build 19041 (2004) and later.

There is no maintained Python binding for this API, so the COM glue is written
here by hand on top of ``comtypes`` (pulled in by pycaw). The public surface
mirrors what ``live_frame`` already consumes from ``soundcard``::

    with ProcessLoopbackRecorder(pid, samplerate=16000) as rec:
        block = rec.record(numframes)   # -> np.ndarray (numframes, channels) float32

so the producer loop does not need to know which backend it is talking to.
"""

import sys

# comtypes initializes the importing thread's COM apartment on import. `soundcard`
# (imported by live_frame) already sets the main thread to MULTITHREADED, and the
# process loopback callback also requires MTA, so force comtypes to match before
# importing it — otherwise `import comtypes` raises RPC_E_CHANGED_MODE.
sys.coinit_flags = 0  # COINIT_MULTITHREADED

import ctypes
import time
from ctypes import POINTER, byref, c_uint32, c_uint64, c_void_p, c_int, c_float
from ctypes.wintypes import DWORD, HANDLE, WORD

import numpy as np
from comtypes import GUID, COMMETHOD, HRESULT, IUnknown, COMObject

# --- WASAPI / Process Loopback constants ------------------------------------

# Virtual device path that routes a process' render stream into a capture client.
VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = "VAD\\Process_Loopback"

AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000

# AUDIOCLIENT_ACTIVATION_TYPE
AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
# PROCESS_LOOPBACK_MODE
PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0

# Audio format tags
WAVE_FORMAT_IEEE_FLOAT = 0x0003

# PROPVARIANT variant type for a counted byte blob.
VT_BLOB = 0x0041

# AUDCLNT_BUFFERFLAGS
AUDCLNT_BUFFERFLAGS_SILENT = 0x2

# Reference time is in 100-ns units. Ask for a generous buffer.
REFTIMES_PER_SEC = 10_000_000


# --- Structures -------------------------------------------------------------


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", WORD),
        ("nChannels", WORD),
        ("nSamplesPerSec", DWORD),
        ("nAvgBytesPerSec", DWORD),
        ("nBlockAlign", WORD),
        ("wBitsPerSample", WORD),
        ("cbSize", WORD),
    ]


class AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS(ctypes.Structure):
    _fields_ = [
        ("TargetProcessId", DWORD),
        ("ProcessLoopbackMode", c_int),
    ]


class AUDIOCLIENT_ACTIVATION_PARAMS(ctypes.Structure):
    # The real struct has a union, but PROCESS_LOOPBACK is the only variant we
    # use, so a flat layout matches the memory layout exactly.
    _fields_ = [
        ("ActivationType", c_int),
        ("ProcessLoopbackParams", AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS),
    ]


class _BLOB(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("pBlobData", c_void_p)]


class PROPVARIANT(ctypes.Structure):
    # Minimal PROPVARIANT: vt + padding + the largest member we touch (BLOB).
    _fields_ = [
        ("vt", WORD),
        ("wReserved1", WORD),
        ("wReserved2", WORD),
        ("wReserved3", WORD),
        ("blob", _BLOB),
        ("_pad", ctypes.c_byte * 8),
    ]


# --- COM interfaces ---------------------------------------------------------


class IAudioCaptureClient(IUnknown):
    _iid_ = GUID("{C8ADBD64-E71E-48a0-A4DE-185C395CD317}")
    _methods_ = [
        COMMETHOD(
            [], HRESULT, "GetBuffer",
            (["out"], POINTER(POINTER(ctypes.c_byte)), "ppData"),
            (["out"], POINTER(c_uint32), "pNumFramesToRead"),
            (["out"], POINTER(DWORD), "pdwFlags"),
            (["out"], POINTER(c_uint64), "pu64DevicePosition"),
            (["out"], POINTER(c_uint64), "pu64QPCPosition"),
        ),
        COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], c_uint32, "NumFramesRead")),
        COMMETHOD(
            [], HRESULT, "GetNextPacketSize",
            (["out"], POINTER(c_uint32), "pNumFramesInNextPacket"),
        ),
    ]


class IAudioClient(IUnknown):
    _iid_ = GUID("{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}")
    _methods_ = [
        COMMETHOD(
            [], HRESULT, "Initialize",
            (["in"], DWORD, "ShareMode"),
            (["in"], DWORD, "StreamFlags"),
            (["in"], ctypes.c_longlong, "hnsBufferDuration"),
            (["in"], ctypes.c_longlong, "hnsPeriodicity"),
            (["in"], POINTER(WAVEFORMATEX), "pFormat"),
            (["in"], POINTER(GUID), "AudioSessionGuid"),
        ),
        COMMETHOD([], HRESULT, "GetBufferSize", (["out"], POINTER(c_uint32), "p")),
        COMMETHOD([], HRESULT, "GetStreamLatency", (["out"], POINTER(ctypes.c_longlong), "p")),
        COMMETHOD([], HRESULT, "GetCurrentPadding", (["out"], POINTER(c_uint32), "p")),
        COMMETHOD(
            [], HRESULT, "IsFormatSupported",
            (["in"], DWORD, "ShareMode"),
            (["in"], POINTER(WAVEFORMATEX), "pFormat"),
            (["out"], POINTER(POINTER(WAVEFORMATEX)), "ppClosestMatch"),
        ),
        COMMETHOD([], HRESULT, "GetMixFormat", (["out"], POINTER(POINTER(WAVEFORMATEX)), "pp")),
        COMMETHOD(
            [], HRESULT, "GetDevicePeriod",
            (["out"], POINTER(ctypes.c_longlong), "pDefault"),
            (["out"], POINTER(ctypes.c_longlong), "pMinimum"),
        ),
        COMMETHOD([], HRESULT, "Start"),
        COMMETHOD([], HRESULT, "Stop"),
        COMMETHOD([], HRESULT, "Reset"),
        COMMETHOD([], HRESULT, "SetEventHandle", (["in"], HANDLE, "eventHandle")),
        COMMETHOD(
            [], HRESULT, "GetService",
            (["in"], POINTER(GUID), "riid"),
            (["out"], POINTER(POINTER(IUnknown)), "ppv"),
        ),
    ]


class IActivateAudioInterfaceAsyncOperation(IUnknown):
    _iid_ = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")
    _methods_ = [
        COMMETHOD(
            [], HRESULT, "GetActivateResult",
            (["out"], POINTER(HRESULT), "activateResult"),
            (["out"], POINTER(POINTER(IUnknown)), "activatedInterface"),
        ),
    ]


class IActivateAudioInterfaceCompletionHandler(IUnknown):
    _iid_ = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
    _methods_ = [
        COMMETHOD(
            [], HRESULT, "ActivateCompleted",
            (["in"], POINTER(IActivateAudioInterfaceAsyncOperation), "activateOperation"),
        ),
    ]


IID_IAudioClient = IAudioClient._iid_
IID_IAudioCaptureClient = IAudioCaptureClient._iid_


class IAgileObject(IUnknown):
    """Marker interface: declares the object safe to call from any apartment.

    ``ActivateAudioInterfaceAsync`` marshals the completion callback across
    threads and rejects a non-agile handler with E_ILLEGAL_METHOD_CALL, so the
    handler must answer QueryInterface(IAgileObject) successfully.
    """

    _iid_ = GUID("{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}")
    _methods_ = []


class _CompletionHandler(COMObject):
    """Receives the async activation result and unblocks the caller."""

    _com_interfaces_ = [IActivateAudioInterfaceCompletionHandler, IAgileObject]

    def __init__(self):
        super().__init__()
        import threading

        self.event = threading.Event()
        self.operation = None

    def ActivateCompleted(self, this, activateOperation):  # noqa: N802 (COM name)
        self.operation = activateOperation
        self.event.set()
        return 0


# --- Recorder ---------------------------------------------------------------


def _make_format(samplerate: int, channels: int) -> WAVEFORMATEX:
    fmt = WAVEFORMATEX()
    fmt.wFormatTag = WAVE_FORMAT_IEEE_FLOAT
    fmt.nChannels = channels
    fmt.nSamplesPerSec = samplerate
    fmt.wBitsPerSample = 32
    fmt.nBlockAlign = channels * 4
    fmt.nAvgBytesPerSec = samplerate * fmt.nBlockAlign
    fmt.cbSize = 0
    return fmt


def list_audio_apps():
    """Return ``[(label, pid), ...]`` for processes with an active audio session.

    Uses pycaw's session enumeration. Sessions without a backing process (system
    sounds) are skipped. Returns an empty list on any failure so the UI can fall
    back to whole-system capture.
    """
    try:
        from pycaw.pycaw import AudioUtilities
    except Exception:
        return []

    apps = {}
    try:
        for session in AudioUtilities.GetAllSessions():
            proc = session.Process
            if proc is None:
                continue
            try:
                pid = proc.pid
                name = proc.name()
            except Exception:
                continue
            # Collapse multiple sessions of the same process into one entry.
            apps.setdefault(pid, name)
    except Exception:
        return []

    return [(name, pid) for pid, name in sorted(apps.items(), key=lambda kv: kv[1].lower())]


class ProcessLoopbackRecorder:
    """Capture one process' render audio as float32 frames.

    Mirrors the ``soundcard`` loopback recorder shape: use as a context manager
    and call ``record(numframes)`` to get an ``(numframes, channels)`` array.
    """

    def __init__(self, pid: int, samplerate: int = 16000, channels: int = 2):
        self._pid = int(pid)
        self._samplerate = samplerate
        self._channels = channels
        self._client = None
        self._capture = None
        self._fmt = _make_format(samplerate, channels)
        self._leftover = np.empty((0, channels), dtype=np.float32)

    # context manager ------------------------------------------------------

    def __enter__(self):
        self._activate()
        return self

    def __exit__(self, *exc):
        try:
            if self._client is not None:
                self._client.Stop()
        except Exception:
            pass
        self._client = None
        self._capture = None
        try:
            ctypes.windll.ole32.CoUninitialize()
        except Exception:
            pass

    # internals ------------------------------------------------------------

    def _activate(self):
        ole32 = ctypes.windll.ole32
        # ActivateAudioInterfaceAsync requires the calling thread to be MTA, and
        # its completion callback arrives on an MTA worker thread. Initialize
        # this thread (the producer thread) as MTA before any COM call.
        ole32.CoInitializeEx(None, 0x2)  # COINIT_MULTITHREADED

        params = AUDIOCLIENT_ACTIVATION_PARAMS()
        params.ActivationType = AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK
        params.ProcessLoopbackParams.TargetProcessId = self._pid
        params.ProcessLoopbackParams.ProcessLoopbackMode = (
            PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE
        )

        prop = PROPVARIANT()
        prop.vt = VT_BLOB
        prop.blob.cbSize = ctypes.sizeof(AUDIOCLIENT_ACTIVATION_PARAMS)
        prop.blob.pBlobData = ctypes.cast(byref(params), c_void_p)

        handler = _CompletionHandler()

        mmdevapi = ctypes.windll.LoadLibrary("Mmdevapi.dll")
        activate = mmdevapi.ActivateAudioInterfaceAsync
        activate.restype = HRESULT
        activate.argtypes = [
            ctypes.c_wchar_p,           # device interface path
            POINTER(GUID),              # riid
            POINTER(PROPVARIANT),       # activation params
            c_void_p,                   # completion handler
            POINTER(c_void_p),          # async operation (out)
        ]

        handler_ptr = handler.QueryInterface(IActivateAudioInterfaceCompletionHandler)
        operation = c_void_p()
        hr = activate(
            VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
            byref(IID_IAudioClient),
            byref(prop),
            ctypes.cast(handler_ptr, c_void_p),  # raw COM pointer to our handler
            byref(operation),
        )
        if hr != 0:
            raise OSError(f"ActivateAudioInterfaceAsync failed: 0x{hr & 0xFFFFFFFF:08X}")

        if not handler.event.wait(timeout=5.0):
            raise TimeoutError("Process loopback activation timed out")

        activate_result, activated = handler.operation.GetActivateResult()
        if activate_result != 0:
            raise OSError(
                f"GetActivateResult failed: 0x{activate_result & 0xFFFFFFFF:08X}"
            )

        self._client = activated.QueryInterface(IAudioClient)
        self._client.Initialize(
            AUDCLNT_SHAREMODE_SHARED,
            AUDCLNT_STREAMFLAGS_LOOPBACK,
            int(REFTIMES_PER_SEC * 2),  # 2s buffer
            0,
            byref(self._fmt),
            None,
        )
        service = self._client.GetService(byref(IID_IAudioCaptureClient))
        self._capture = service.QueryInterface(IAudioCaptureClient)
        self._client.Start()

    def _pull(self) -> np.ndarray:
        """Drain whatever packets are currently available into a float32 array."""
        chunks = []
        while True:
            avail = self._capture.GetNextPacketSize()
            if avail == 0:
                break
            data_ptr, frames, flags, _devpos, _qpc = self._capture.GetBuffer()
            if frames == 0:
                break
            if flags & AUDCLNT_BUFFERFLAGS_SILENT:
                block = np.zeros((frames, self._channels), dtype=np.float32)
            else:
                float_ptr = ctypes.cast(data_ptr, POINTER(c_float))
                count = frames * self._channels
                block = np.ctypeslib.as_array(float_ptr, shape=(count,)).copy()
                block = block.reshape(frames, self._channels)
            chunks.append(block)
            self._capture.ReleaseBuffer(frames)
        if chunks:
            return np.concatenate(chunks, axis=0)
        return np.empty((0, self._channels), dtype=np.float32)

    def record(self, numframes: int) -> np.ndarray:
        """Block until ``numframes`` frames are available, return them.

        Matches the ``soundcard`` recorder contract used by the producer.
        """
        buf = [self._leftover]
        have = self._leftover.shape[0]
        while have < numframes:
            pulled = self._pull()
            if pulled.shape[0]:
                buf.append(pulled)
                have += pulled.shape[0]
            else:
                time.sleep(0.01)
        data = np.concatenate(buf, axis=0)
        out = data[:numframes]
        self._leftover = data[numframes:]
        return out
