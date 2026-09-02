"""Voice recording tool for creating a new TTS voice.

Walks through every sample name the phonology engine can actually select
(korean_tts.all_reachable_samples()), shows a real Korean character to say
for each one, records it via mic_record.MicRecorder, and saves accepted
takes directly as sound/<voice>/<name>.wav - which the existing multi-voice
system (korean_tts.list_voices/voice_dir, already used by tts.py/gui.py/
Android/web) picks up immediately. No separate "build a voice" step: once
recorded, `py tts.py --voice <name> ...` (or the GUI's voice dropdown)
already works.

Packaged into a standalone .exe via PyInstaller (see BUILD_EXE.txt); run
directly with `py voice_recorder.py` otherwise. Pure standard library.
Recording itself (mic_record.py) is Windows-only (raw waveIn via ctypes) -
on any other OS, or if no microphone is found, this still runs as a
prompt-list viewer (see PromptTab) so it's still useful without a mic.
"""

import os
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import korean_tts as ktts

try:
    import mic_record
    _MIC_IMPORT_ERROR = None
except Exception as e:  # pragma: no cover - only exercised on non-Windows
    mic_record = None
    _MIC_IMPORT_ERROR = e

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Same convention as gui.py: a standalone exe writes next to itself, not
# into whatever ephemeral extraction folder PyInstaller used this launch.
# Run as a plain script inside this folder, SOUND_ROOT is the real, existing
# sound/ - new voices land exactly where sync-sound-assets.bat expects them.
if getattr(sys, "frozen", False):
    WRITABLE_DIR = os.path.dirname(sys.executable)
else:
    WRITABLE_DIR = BASE_DIR
SOUND_ROOT = os.path.join(WRITABLE_DIR, "sound")


def _compose(cho: str, jung: str, jong: str) -> str:
    return chr(0xAC00 + (ktts.CHO_INDEX[cho] * 21 + ktts.JUNG_INDEX[jung]) * 28 + ktts.JONG_INDEX[jong])


def build_prompt_map() -> dict:
    """sample name -> a real Hangul character that, said aloud, produces
    exactly that recording. Covers every name all_reachable_samples() can
    return (verified: build_prompt_map's keys are a superset, with 0 gaps).

    Two pieces make up almost every name: an onset+vowel ("ga") or a
    silent-onset vowel(+stop-coda) ("ag") - each has a natural single
    Hangul character that IS exactly that recording (가, 악). The four bare
    sonorant-coda tails (n/l/m/ng) are the one exception: the library
    records those as a mini-syllable with ㅡ (느/르/므/응), matching the
    convention already baked into CODA_MAX_MS/CODA_TAIL_FADE_MS's tuning -
    so a new voice's recordings behave the same way under the same timing
    constants.
    """
    examples = {}
    simple_vowels = [v for v in ktts.JUNGSEONG if v in ktts.ROMAN]

    for cho in ktts.CHOSEONG:
        for jung in simple_vowels:
            name = ktts.ROMAN[cho] + ktts.ROMAN[jung]
            examples.setdefault(name, _compose(cho, jung, ""))

    stop_jong = [j for j in ktts.JONGSEONG if j in ("ㄱ", "ㄷ", "ㅂ")]
    for jung in simple_vowels:
        examples.setdefault(ktts.ROMAN[jung], _compose("ㅇ", jung, ""))
        for jong in stop_jong:
            name = ktts.ROMAN[jung] + ktts.ROMAN[jong]
            examples.setdefault(name, _compose("ㅇ", jung, jong))

    examples["n"] = "느"
    examples["l"] = "르"
    examples["m"] = "므"
    examples["ng"] = "응"
    return examples


def to_wav_bytes(pcm: bytes) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(mic_record.CHANNELS if mic_record else 1)
        w.setsampwidth(mic_record.SAMPLE_WIDTH if mic_record else 2)
        w.setframerate(mic_record.TARGET_RATE if mic_record else ktts.TARGET_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("한국어 TTS - 목소리 녹음")
        self.geometry("720x560")

        self.prompts = build_prompt_map()
        self.names = sorted(ktts.all_reachable_samples())
        self.index = 0
        self.voice = tk.StringVar(value="")
        self.current_take = None  # bytes of the just-recorded, not-yet-saved take
        self.recorder = None
        self.recording = False

        self._build_ui()
        self._refresh_voice_list()

    # ------------------------------------------------------
    # UI
    # ------------------------------------------------------

    def _build_ui(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=8)
        ttk.Label(top, text="목소리 이름:").pack(side="left")
        self.voice_combo = ttk.Combobox(top, textvariable=self.voice, width=24)
        self.voice_combo.pack(side="left", padx=6)
        self.voice_combo.bind("<<ComboboxSelected>>", lambda _e: self._load_voice())
        ttk.Button(top, text="시작/이어하기", command=self._load_voice).pack(side="left", padx=4)
        self.voice_hint = ttk.Label(top, text="", foreground="#888")
        self.voice_hint.pack(side="left", padx=10)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=10, pady=4)

        list_frame = ttk.Frame(body)
        list_frame.pack(side="left", fill="y")
        ttk.Label(list_frame, text="전체 목록").pack(anchor="w")
        self.listbox = tk.Listbox(list_frame, width=14, height=26)
        self.listbox.pack(side="left", fill="y")
        scrollbar = ttk.Scrollbar(list_frame, command=self.listbox.yview)
        scrollbar.pack(side="left", fill="y")
        self.listbox.config(yscrollcommand=scrollbar.set)
        self.listbox.bind("<<ListboxSelect>>", self._on_list_select)
        for name in self.names:
            self.listbox.insert("end", name)

        main = ttk.Frame(body)
        main.pack(side="left", fill="both", expand=True, padx=16)

        self.progress_label = ttk.Label(main, text="", foreground="#555")
        self.progress_label.pack(anchor="w")

        self.prompt_label = tk.Label(main, text="", font=("", 72, "bold"))
        self.prompt_label.pack(pady=(20, 4))

        self.name_label = ttk.Label(main, text="", foreground="#888")
        self.name_label.pack()

        self.status_label = ttk.Label(main, text="", foreground="#0a0")
        self.status_label.pack(pady=8)

        btns = ttk.Frame(main)
        btns.pack(pady=12)
        self.record_btn = ttk.Button(btns, text="● 녹음 시작", command=self._toggle_record)
        self.record_btn.pack(side="left", padx=4)
        self.preview_btn = ttk.Button(btns, text="▶ 들어보기", command=self._preview, state="disabled")
        self.preview_btn.pack(side="left", padx=4)
        self.accept_btn = ttk.Button(btns, text="저장하고 다음", command=self._accept_and_next, state="disabled")
        self.accept_btn.pack(side="left", padx=4)

        nav = ttk.Frame(main)
        nav.pack(pady=4)
        ttk.Button(nav, text="◀ 이전", command=self._prev).pack(side="left", padx=4)
        ttk.Button(nav, text="다음 (건너뛰기) ▶", command=self._next).pack(side="left", padx=4)

        note = ("녹음 후 '저장하고 다음'을 누르면 sound\\<목소리>\\<이름>.wav 로 바로 저장됩니다. "
                "이미 있는 항목을 다시 녹음하면 덮어씁니다. 다 끝나면 sync-sound-assets.bat 을 "
                "실행해서 Android/웹 버전에도 반영하세요.")
        ttk.Label(main, text=note, wraplength=440, foreground="#888").pack(side="bottom", pady=8)

        if mic_record is None or not mic_record.is_available():
            self.record_btn.config(state="disabled")
            reason = str(_MIC_IMPORT_ERROR) if _MIC_IMPORT_ERROR else "마이크를 찾을 수 없습니다."
            ttk.Label(main, text=f"⚠ 녹음 기능을 쓸 수 없습니다 ({reason}) - 목록만 보여줍니다.",
                      foreground="#a00", wraplength=440).pack(side="bottom", pady=4)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _refresh_voice_list(self):
        existing = [v for v in ktts.list_voices(SOUND_ROOT) if v != ktts.DEFAULT_VOICE]
        self.voice_combo["values"] = existing

    # ------------------------------------------------------
    # Voice / navigation
    # ------------------------------------------------------

    def _load_voice(self):
        name = self.voice.get().strip()
        if not name:
            messagebox.showinfo("한국어 TTS", "목소리 이름을 입력하세요 (예: narrator2).")
            return
        if name == ktts.DEFAULT_VOICE:
            messagebox.showwarning("한국어 TTS", "'default'는 원래 목소리 이름이라 다른 이름을 쓰세요.")
            return
        invalid = set(name) & set('\\/:*?"<>| ')
        if name.startswith(("_", ".")) or invalid:
            messagebox.showwarning(
                "한국어 TTS",
                "목소리 이름은 밑줄(_)/마침표(.)로 시작할 수 없고 공백이나 \\/:*?\"<>| 를 "
                "쓸 수 없습니다 (안드로이드가 그런 assets 폴더를 자동으로 무시합니다).",
            )
            return

        self.voice_dir = os.path.join(SOUND_ROOT, name)
        os.makedirs(self.voice_dir, exist_ok=True)
        self._refresh_voice_list()
        self._refresh_done_markers()
        self.index = self._first_unrecorded_index()
        self._show_current()
        self.record_btn.config(state="normal" if (mic_record and mic_record.is_available()) else "disabled")

    def _refresh_done_markers(self):
        self.listbox.delete(0, "end")
        for name in self.names:
            done = os.path.exists(os.path.join(self.voice_dir, name + ".wav"))
            self.listbox.insert("end", ("✔ " if done else "   ") + name)

    def _first_unrecorded_index(self) -> int:
        for i, name in enumerate(self.names):
            if not os.path.exists(os.path.join(self.voice_dir, name + ".wav")):
                return i
        return 0

    def _current_name(self) -> str:
        return self.names[self.index]

    def _show_current(self):
        name = self._current_name()
        done_count = sum(
            1 for n in self.names if os.path.exists(os.path.join(self.voice_dir, n + ".wav"))
        )
        self.progress_label.config(text=f"{self.index + 1} / {len(self.names)}  (완료: {done_count}개)")
        self.prompt_label.config(text=self.prompts.get(name, "?"))
        self.name_label.config(text=f"조각 이름: {name}")
        self.status_label.config(text="")
        self.current_take = None
        self.preview_btn.config(state="disabled")
        self.accept_btn.config(state="disabled")
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(self.index)
        self.listbox.see(self.index)

    def _on_list_select(self, _evt=None):
        selection = self.listbox.curselection()
        if not selection or not hasattr(self, "voice_dir"):
            return
        self.index = selection[0]
        self._show_current()

    def _prev(self):
        if not hasattr(self, "voice_dir"):
            return
        self.index = (self.index - 1) % len(self.names)
        self._show_current()

    def _next(self):
        if not hasattr(self, "voice_dir"):
            return
        self.index = (self.index + 1) % len(self.names)
        self._show_current()

    # ------------------------------------------------------
    # Recording
    # ------------------------------------------------------

    def _toggle_record(self):
        if not self.recording:
            try:
                self.recorder = mic_record.MicRecorder(max_seconds=12)
                self.recorder.start()
            except mic_record.RecordingError as e:
                messagebox.showerror("한국어 TTS", str(e))
                return
            self.recording = True
            self.record_btn.config(text="■ 녹음 중지")
            self.status_label.config(text="🔴 녹음 중...")
            self.preview_btn.config(state="disabled")
            self.accept_btn.config(state="disabled")
        else:
            pcm = self.recorder.stop()
            self.recording = False
            self.record_btn.config(text="● 녹음 시작")
            if len(pcm) < mic_record.TARGET_RATE * mic_record.SAMPLE_WIDTH // 10:  # < ~0.1s
                self.status_label.config(text="너무 짧습니다 - 다시 녹음해보세요.")
                self.current_take = None
                self.preview_btn.config(state="disabled")
                self.accept_btn.config(state="disabled")
                return
            self.current_take = pcm
            self.status_label.config(text=f"녹음됨 ({len(pcm) / (mic_record.TARGET_RATE * mic_record.SAMPLE_WIDTH):.2f}초) - 들어보고 저장하세요.")
            self.preview_btn.config(state="normal")
            self.accept_btn.config(state="normal")

    def _preview(self):
        if not self.current_take:
            return
        wav_bytes = to_wav_bytes(self.current_take)

        def work():
            try:
                ktts.play(wav_bytes)
            except ktts.AudioError:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _accept_and_next(self):
        if not self.current_take or not hasattr(self, "voice_dir"):
            return
        name = self._current_name()
        path = os.path.join(self.voice_dir, name + ".wav")
        try:
            with open(path, "wb") as f:
                f.write(to_wav_bytes(self.current_take))
        except OSError as e:
            messagebox.showerror("한국어 TTS", f"저장 실패: {e}")
            return
        self._refresh_done_markers()
        if self.index + 1 < len(self.names):
            self.index += 1
        self._show_current()

    def _on_close(self):
        if self.recorder is not None:
            self.recorder.cancel()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
