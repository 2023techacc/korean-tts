"""Microphone recording via the raw Win32 waveIn API (winmm.dll), through
ctypes - no pyaudio/sounddevice/extra pip installs, matching this project's
zero-dependency approach everywhere else. Windows-only (the desktop voice
recorder tool is Windows-only anyway, same as gui.py's winsound playback).

The technique: open a device, queue ONE buffer sized for the longest take
you'll ever allow, start capture, and later call waveInReset() to stop and
flush early - Windows fills in the buffer's dwBytesRecorded with exactly how
much was captured up to that point, even though the buffer never filled.
That's the standard way to do a variable-length recording with the raw
waveIn API (no callback/threading needed for this simple start/stop case).
"""

import ctypes
import sys
from ctypes import wintypes

TARGET_RATE = 44100
SAMPLE_WIDTH = 2  # 16-bit
CHANNELS = 1


class RecordingError(RuntimeError):
    pass


if sys.platform == "win32":
    _winmm = ctypes.windll.winmm

    _CALLBACK_NULL = 0x00000000
    _WAVE_MAPPER = 0xFFFFFFFF
    _WAVE_FORMAT_PCM = 1

    class _WAVEFORMATEX(ctypes.Structure):
        _fields_ = [
            ("wFormatTag", wintypes.WORD),
            ("nChannels", wintypes.WORD),
            ("nSamplesPerSec", wintypes.DWORD),
            ("nAvgBytesPerSec", wintypes.DWORD),
            ("nBlockAlign", wintypes.WORD),
            ("wBitsPerSample", wintypes.WORD),
            ("cbSize", wintypes.WORD),
        ]

    class _WAVEHDR(ctypes.Structure):
        pass

    _WAVEHDR._fields_ = [
        ("lpData", ctypes.c_void_p),
        ("dwBufferLength", wintypes.DWORD),
        ("dwBytesRecorded", wintypes.DWORD),
        ("dwUser", ctypes.c_void_p),
        ("dwFlags", wintypes.DWORD),
        ("dwLoops", wintypes.DWORD),
        ("lpNext", ctypes.POINTER(_WAVEHDR)),
        ("reserved", ctypes.c_void_p),
    ]

    _HWAVEIN = wintypes.HANDLE

    _winmm.waveInGetNumDevs.argtypes = []
    _winmm.waveInGetNumDevs.restype = wintypes.UINT

    _winmm.waveInOpen.argtypes = [
        ctypes.POINTER(_HWAVEIN), wintypes.UINT, ctypes.POINTER(_WAVEFORMATEX),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
    ]
    _winmm.waveInOpen.restype = wintypes.UINT

    _winmm.waveInPrepareHeader.argtypes = [_HWAVEIN, ctypes.POINTER(_WAVEHDR), wintypes.UINT]
    _winmm.waveInPrepareHeader.restype = wintypes.UINT
    _winmm.waveInUnprepareHeader.argtypes = [_HWAVEIN, ctypes.POINTER(_WAVEHDR), wintypes.UINT]
    _winmm.waveInUnprepareHeader.restype = wintypes.UINT
    _winmm.waveInAddBuffer.argtypes = [_HWAVEIN, ctypes.POINTER(_WAVEHDR), wintypes.UINT]
    _winmm.waveInAddBuffer.restype = wintypes.UINT
    _winmm.waveInStart.argtypes = [_HWAVEIN]
    _winmm.waveInStart.restype = wintypes.UINT
    _winmm.waveInStop.argtypes = [_HWAVEIN]
    _winmm.waveInStop.restype = wintypes.UINT
    _winmm.waveInReset.argtypes = [_HWAVEIN]
    _winmm.waveInReset.restype = wintypes.UINT
    _winmm.waveInClose.argtypes = [_HWAVEIN]
    _winmm.waveInClose.restype = wintypes.UINT


def is_available() -> bool:
    """Whether there's at least one waveIn-capable input device at all."""
    if sys.platform != "win32":
        return False
    return _winmm.waveInGetNumDevs() > 0


class MicRecorder:
    """One recording at a time: start(), then stop() to get back the
    captured PCM as bytes (mono, 16-bit, TARGET_RATE). Not reusable after
    stop() - make a new instance for the next take."""

    def __init__(self, max_seconds: float = 12.0):
        if sys.platform != "win32":
            raise RecordingError("마이크 녹음은 Windows에서만 지원됩니다.")
        self._max_seconds = max_seconds
        self._hwi = None
        self._hdr = None
        self._buf = None

    def start(self) -> None:
        if self._hwi is not None:
            raise RecordingError("이미 녹음 중입니다.")

        wfx = _WAVEFORMATEX()
        wfx.wFormatTag = _WAVE_FORMAT_PCM
        wfx.nChannels = CHANNELS
        wfx.nSamplesPerSec = TARGET_RATE
        wfx.wBitsPerSample = SAMPLE_WIDTH * 8
        wfx.nBlockAlign = CHANNELS * SAMPLE_WIDTH
        wfx.nAvgBytesPerSec = TARGET_RATE * wfx.nBlockAlign
        wfx.cbSize = 0

        hwi = _HWAVEIN()
        rc = _winmm.waveInOpen(ctypes.byref(hwi), _WAVE_MAPPER, ctypes.byref(wfx), None, None, _CALLBACK_NULL)
        if rc != 0:
            raise RecordingError(f"마이크를 열 수 없습니다 (waveInOpen 오류 {rc}) - 마이크가 연결되어 있고 다른 프로그램이 독점하고 있지 않은지 확인하세요.")

        buf_size = int(TARGET_RATE * wfx.nBlockAlign * self._max_seconds)
        buf = ctypes.create_string_buffer(buf_size)
        hdr = _WAVEHDR()
        hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
        hdr.dwBufferLength = buf_size
        hdr.dwFlags = 0
        hdr.dwLoops = 0

        rc = _winmm.waveInPrepareHeader(hwi, ctypes.byref(hdr), ctypes.sizeof(hdr))
        if rc != 0:
            _winmm.waveInClose(hwi)
            raise RecordingError(f"녹음 버퍼 준비 실패 ({rc})")

        rc = _winmm.waveInAddBuffer(hwi, ctypes.byref(hdr), ctypes.sizeof(hdr))
        if rc != 0:
            _winmm.waveInUnprepareHeader(hwi, ctypes.byref(hdr), ctypes.sizeof(hdr))
            _winmm.waveInClose(hwi)
            raise RecordingError(f"녹음 버퍼 등록 실패 ({rc})")

        rc = _winmm.waveInStart(hwi)
        if rc != 0:
            _winmm.waveInUnprepareHeader(hwi, ctypes.byref(hdr), ctypes.sizeof(hdr))
            _winmm.waveInClose(hwi)
            raise RecordingError(f"녹음 시작 실패 ({rc})")

        self._hwi = hwi
        self._hdr = hdr
        self._buf = buf  # keep alive - the OS is writing into this buffer

    def stop(self) -> bytes:
        """Stop capture and return the PCM bytes recorded so far (mono,
        16-bit, TARGET_RATE) - empty bytes if start() was never called."""
        if self._hwi is None:
            return b""
        _winmm.waveInStop(self._hwi)
        _winmm.waveInReset(self._hwi)  # flushes the in-progress buffer, finalizing dwBytesRecorded
        n = self._hdr.dwBytesRecorded
        data = ctypes.string_at(self._buf, n)
        _winmm.waveInUnprepareHeader(self._hwi, ctypes.byref(self._hdr), ctypes.sizeof(self._hdr))
        _winmm.waveInClose(self._hwi)
        self._hwi = None
        self._hdr = None
        self._buf = None
        return data

    def cancel(self) -> None:
        """Stop and discard, e.g. if the window is closing mid-recording."""
        if self._hwi is not None:
            self.stop()
