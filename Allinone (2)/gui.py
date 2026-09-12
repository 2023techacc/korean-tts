"""Korean TTS desktop app - text entry + playback controls, plus a
per-sample fine-tuning panel for the sound/ library. Packaged into a
standalone .exe via PyInstaller (see BUILD_EXE.txt); run directly with
`py gui.py` otherwise. Pure standard library (tkinter ships with Python).

Same engine as tts.py/main.py (korean_tts.py) - nothing here duplicates the
phonology or audio pipeline, only the UI and the fine-tuning overrides file
that pipeline already knows how to consume (see korean_tts.load_overrides).
"""

import array
import io
import json
import os
import sys
import tempfile
import threading
import tkinter as tk
import wave
from tkinter import filedialog, messagebox, ttk

import korean_tts as ktts

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# When packaged by PyInstaller (--onefile), bundled data (sound/) is
# extracted fresh into a temp dir (sys._MEIPASS) on every launch — writing
# settings/overrides there would silently lose them every run. Read-only
# bundled assets come from _MEIPASS when frozen; anything this app writes
# goes next to the .exe itself instead, which persists across runs. Running
# as a plain script (not frozen) keeps the original "next to gui.py" layout.
if getattr(sys, "frozen", False):
    RESOURCE_DIR = getattr(sys, "_MEIPASS", BASE_DIR)
    WRITABLE_DIR = os.path.dirname(sys.executable)
else:
    RESOURCE_DIR = BASE_DIR
    WRITABLE_DIR = BASE_DIR

# Not a true constant once running: App.reload_sound_from_disk() can
# reassign this (via `global SOUND_ROOT`) to WRITABLE_DIR's own sound/ -
# the live folder VoiceRecorder.exe actually edits - so an already-running
# packaged exe can pick up those edits without a full rebuild. Every
# reader below looks this name up at call time, not at import time, so
# that reassignment takes effect everywhere without further changes.
SOUND_ROOT = os.path.join(RESOURCE_DIR, "sound")
OVERRIDES_DIR = os.path.join(WRITABLE_DIR, ktts.OVERRIDES_SUBDIR)
ktts.migrate_legacy_overrides(WRITABLE_DIR, OVERRIDES_DIR)
OVERRIDES_PATH = os.path.join(OVERRIDES_DIR, ktts.DEFAULT_OVERRIDES_FILENAME)
SETTINGS_PATH = os.path.join(WRITABLE_DIR, "gui_settings.json")


# ------------------------------------------------------
# Non-blocking playback
# ------------------------------------------------------

class Player:
    """Plays WAV bytes without freezing the UI thread.

    Windows uses winsound's async mode - but PlaySound raises
    "Cannot play asynchronously from memory" if you combine SND_MEMORY with
    SND_ASYNC (confirmed by actually calling it, not just from docs), so
    async playback has to go through a temp file instead. The temp file
    can't be deleted immediately after starting playback (Windows is still
    reading it); it's cleaned up lazily on the next play()/stop() instead,
    by which point PURGE has stopped the previous playback and released it.
    Elsewhere, the existing blocking korean_tts.play() runs on a background
    thread instead.
    """

    def __init__(self):
        self._token = 0  # bumped on every play()/stop() so a stale timer's on_done is ignored
        self._temp_path = None

    def play(self, wav_bytes: bytes, on_done=None):
        self.stop()
        self._token += 1
        token = self._token

        if sys.platform == "win32":
            import winsound
            fd, path = tempfile.mkstemp(suffix=".wav", prefix="ktts_")
            with os.fdopen(fd, "wb") as f:
                f.write(wav_bytes)
            self._temp_path = path
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            if on_done:
                with wave.open(io.BytesIO(wav_bytes)) as w:
                    duration = w.getnframes() / w.getframerate()
                threading.Thread(target=self._notify_after, args=(duration, token, on_done), daemon=True).start()
        else:
            threading.Thread(target=self._play_blocking, args=(wav_bytes, token, on_done), daemon=True).start()

    def _notify_after(self, duration, token, on_done):
        threading.Event().wait(duration)
        if token == self._token:
            on_done()

    def _play_blocking(self, wav_bytes, token, on_done):
        try:
            ktts.play(wav_bytes)
        except ktts.AudioError:
            pass
        if token == self._token and on_done:
            on_done()

    def stop(self):
        self._token += 1  # invalidate any pending _notify_after
        if sys.platform == "win32":
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
            if self._temp_path and os.path.exists(self._temp_path):
                try:
                    os.remove(self._temp_path)
                except OSError:
                    pass  # still locked somehow; harmless leftover in the OS temp dir
                self._temp_path = None


# ------------------------------------------------------
# Persisted GUI settings (speed/gap/volume/stop-gap sliders)
# ------------------------------------------------------

DEFAULT_SETTINGS = {
    "speed": 1.0, "gap_ms": 300, "volume": 1.0, "stop_gap_ms": ktts.DEFAULT_STOP_GAP_MS,
    "voice": ktts.DEFAULT_VOICE, "speed_method": ktts.DEFAULT_SPEED_METHOD,
}


def load_settings() -> dict:
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {**DEFAULT_SETTINGS, **data}
    except (OSError, ValueError):
        pass
    return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict) -> None:
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except OSError:
        pass


# ------------------------------------------------------
# Main window
# ------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("한국어 TTS")
        self.geometry("900x760")

        self.player = Player()
        self.settings = load_settings()
        if self.settings["voice"] not in ktts.list_voices(SOUND_ROOT):
            self.settings["voice"] = ktts.DEFAULT_VOICE  # a saved voice folder went missing
        self.overrides = ktts.load_overrides(self.overrides_path())

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)

        self.main_tab = MainTab(notebook, self)
        self.tuning_tab = TuningTab(notebook, self)
        self.batch_tab = BatchTab(notebook, self)
        notebook.add(self.main_tab, text="재생")
        notebook.add(self.tuning_tab, text="고급 설정 (음성 조각별 미세조정)")
        notebook.add(self.batch_tab, text="일괄 변환")

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def current_sound_dir(self) -> str:
        return ktts.voice_dir(SOUND_ROOT, self.settings["voice"])

    def overrides_path(self) -> str:
        return ktts.overrides_path_for_voice(OVERRIDES_PATH, self.settings["voice"])

    def bank_manifest(self) -> dict:
        return ktts.load_bank_manifest(self.current_sound_dir())

    def audio_settings(self):
        return ktts.resolve_audio_settings(self.bank_manifest())

    def set_bank_audio_setting(self, key: str, value) -> None:
        """Merge one key into the current voice's bank.json "audio" block
        (creating the file if it doesn't exist yet, e.g. sound/narrator2/
        with no bank.json at all) and save - value=None removes the key
        instead, reverting to the module-level default. Used by the
        TuningTab's whole-voice settings (e.g. global crossfade length),
        which apply regardless of which sample is selected, unlike every
        other slider in that tab."""
        manifest = self.bank_manifest()
        audio = dict(manifest.get("audio") or {})
        if value is None:
            audio.pop(key, None)
        else:
            audio[key] = value
        manifest["audio"] = audio
        ktts.save_bank_manifest(self.current_sound_dir(), manifest)

    def set_voice(self, voice: str) -> None:
        """Switch the active voice: persist the outgoing voice's overrides
        (so in-progress fine-tuning is never silently lost on switch), then
        load the new voice's own overrides and refresh the tuning tab."""
        if voice == self.settings["voice"]:
            return
        try:
            ktts.save_overrides(self.overrides_path(), self.overrides)
        except OSError:
            pass
        self.settings["voice"] = voice
        self.overrides = ktts.load_overrides(self.overrides_path())
        self.tuning_tab.on_voice_changed()

    def reload_sound_from_disk(self) -> None:
        """Re-point SOUND_ROOT at the real, live sound/ folder next to the
        .exe (WRITABLE_DIR) instead of the frozen snapshot PyInstaller baked
        into the exe at build time (RESOURCE_DIR, from sys._MEIPASS once
        packaged - see the module-level comment above SOUND_ROOT). Lets an
        already-running KoreanTTS.exe pick up edits made in VoiceRecorder.
        exe - which always writes straight to that same live folder -
        without a full PyInstaller rebuild. A no-op with an explanatory
        message when there's nothing to switch to: already running
        unpackaged (`py gui.py`, where SOUND_ROOT is already that live
        folder), or a packaged exe with no sound/ folder actually sitting
        next to it (a plain end-user install with only the bundled copy).
        Never touches self.overrides - that's loaded from WRITABLE_DIR
        (already live either way) and may hold in-memory tuning changes
        not yet written to disk via '모든 변경사항 저장'.
        """
        global SOUND_ROOT
        live_dir = os.path.join(WRITABLE_DIR, "sound")
        if os.path.normcase(os.path.abspath(live_dir)) == os.path.normcase(os.path.abspath(SOUND_ROOT)):
            messagebox.showinfo("한국어 TTS", "이미 실시간 폴더에서 불러오고 있습니다 (더 새로고침할 것이 없음).")
            return
        if not os.path.isdir(live_dir):
            messagebox.showinfo(
                "한국어 TTS", f"실행 파일 옆에 sound 폴더가 없어 새로고침할 것이 없습니다:\n{live_dir}"
            )
            return

        SOUND_ROOT = live_dir
        if self.settings["voice"] not in ktts.list_voices(SOUND_ROOT):
            self.settings["voice"] = ktts.DEFAULT_VOICE
        self.main_tab.voice_combo["values"] = ktts.list_voices(SOUND_ROOT)
        self.main_tab.voice_var.set(self.settings["voice"])
        self.tuning_tab.on_voice_changed()
        messagebox.showinfo("한국어 TTS", f"실시간 폴더에서 다시 불러왔습니다:\n{live_dir}")

    def _on_close(self):
        self.player.stop()
        save_settings(self.settings)
        self.destroy()


class MainTab(ttk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent)
        self.app = app
        self._last_track = None  # last built samples, for :save-equivalent without rebuilding
        self.position_overrides = {}  # {syllable index in current text: {gain_db, ...}} - session-only, see on_position_overrides
        self._position_overrides_text = None  # the text position_overrides was built against - see _build

        ttk.Label(self, text="읽을 한국어를 입력하세요").pack(anchor="w", padx=8, pady=(8, 0))
        self.text_box = tk.Text(self, height=5, wrap="word")
        self.text_box.pack(fill="x", padx=8, pady=4)

        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x", padx=8, pady=4)
        ttk.Button(btn_row, text="재생", command=self.on_play).pack(side="left", padx=(0, 4))
        ttk.Button(btn_row, text="정지", command=self.app.player.stop).pack(side="left", padx=4)
        ttk.Button(btn_row, text="발음 보기", command=self.on_show_pronunciation).pack(side="left", padx=4)
        ttk.Button(btn_row, text="WAV로 저장", command=self.on_save).pack(side="left", padx=4)
        ttk.Button(btn_row, text="위치별 세부 조정", command=self.on_position_overrides).pack(side="left", padx=4)

        self.pron_label = ttk.Label(self, text="", wraplength=600, foreground="#555")
        self.pron_label.pack(anchor="w", padx=8, pady=(0, 8))

        sliders = ttk.LabelFrame(self, text="설정")
        sliders.pack(fill="x", padx=8, pady=8)

        voice_row = ttk.Frame(sliders)
        voice_row.pack(fill="x", padx=8, pady=4)
        ttk.Label(voice_row, text="목소리", width=26).pack(side="left")
        self.voice_var = tk.StringVar(value=self.app.settings["voice"])
        self.voice_combo = ttk.Combobox(voice_row, textvariable=self.voice_var, state="readonly",
                                         values=ktts.list_voices(SOUND_ROOT))
        self.voice_combo.pack(side="left", fill="x", expand=True, padx=8)
        self.voice_combo.bind("<<ComboboxSelected>>", lambda _evt: self.app.set_voice(self.voice_var.get()))
        ttk.Button(voice_row, text="새로고침 (디스크에서 다시 불러오기)",
                   command=self.app.reload_sound_from_disk).pack(side="left")

        s = self.app.settings
        self.speed_var = self._add_slider(sliders, "재생 속도", s["speed"], ktts.MIN_SPEED, ktts.MAX_SPEED,
                                           fmt=lambda v: f"{v:.2f}x", key="speed")

        speed_method_row = ttk.Frame(sliders)
        speed_method_row.pack(fill="x", padx=8, pady=4)
        ttk.Label(speed_method_row, text="속도 변경 방식", width=26).pack(side="left")
        self._speed_method_labels = {
            "resample": "일반 (빠름, 음높이도 변함)",
            "wsola": "음높이 유지 (WSOLA, 느릴 수 있음)",
        }
        self._speed_method_values = {v: k for k, v in self._speed_method_labels.items()}
        self.speed_method_var = tk.StringVar(
            value=self._speed_method_labels.get(s["speed_method"], self._speed_method_labels["resample"])
        )
        speed_method_combo = ttk.Combobox(
            speed_method_row, textvariable=self.speed_method_var, state="readonly",
            values=list(self._speed_method_labels.values()), width=32,
        )
        speed_method_combo.pack(side="left", padx=8)
        speed_method_combo.bind(
            "<<ComboboxSelected>>",
            lambda _evt: self.app.settings.__setitem__(
                "speed_method", self._speed_method_values[self.speed_method_var.get()]
            ),
        )

        self.gap_var = self._add_slider(sliders, "띄어쓰기 간격", s["gap_ms"], 0, 800,
                                         fmt=lambda v: f"{int(v)}ms", key="gap_ms")
        self.volume_var = self._add_slider(sliders, "볼륨", s["volume"] * 100, 0, 100,
                                            fmt=lambda v: f"{int(v)}%", key="volume", scale=0.01)
        self.stop_gap_var = self._add_slider(sliders, "받침 ㄱㄷㅂ 뒤 간격", s["stop_gap_ms"], 0, 200,
                                              fmt=lambda v: f"{int(v)}ms", key="stop_gap_ms")

        note = ("다른 프로그램에서도 쓰려면: tts.py 는 같은 설정을 --speed/--gap/"
                "--stop-gap 플래그로 받고, 고급 설정 탭에서 저장한 음성 조각별 "
                "미세조정은 자동으로 같이 적용됩니다.")
        ttk.Label(self, text=note, wraplength=600, foreground="#888").pack(anchor="w", padx=8, pady=4)

    def _add_slider(self, parent, label, value, lo, hi, fmt, key, scale=1.0):
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=8, pady=4)
        text_var = tk.StringVar(value=f"{label}: {fmt(value)}")
        ttk.Label(row, textvariable=text_var, width=26).pack(side="left")
        var = tk.DoubleVar(value=value)

        def on_change(_evt=None):
            v = var.get()
            text_var.set(f"{label}: {fmt(v)}")
            self.app.settings[key] = v * scale if scale != 1.0 else v

        scale_widget = ttk.Scale(row, from_=lo, to=hi, orient="horizontal", variable=var, command=lambda _v: on_change())
        scale_widget.pack(side="left", fill="x", expand=True, padx=8)
        return var

    def current_text(self) -> str:
        return self.text_box.get("1.0", "end").strip()

    def on_show_pronunciation(self):
        text = self.current_text()
        if not text:
            return
        self.pron_label.config(text=f"발음: {ktts.text_to_pronunciation(text)}")

    def _build(self, text: str):
        # position_overrides is keyed by syllable index in the EXACT text it
        # was set up against (see on_position_overrides) - if the text box
        # has since been edited, those indices may no longer point at the
        # syllables the user actually adjusted, so they're silently not
        # applied rather than risk hitting the wrong syllable. Reopening
        # "위치별 세부 조정" against the new text re-establishes them.
        position_overrides = (
            self.position_overrides
            if self.position_overrides and self._position_overrides_text == text
            else None
        )
        return ktts.synthesize(
            text, SOUND_ROOT, self.app.settings["voice"],
            gap_ms=int(self.app.settings["gap_ms"]),
            stop_gap_ms=int(self.app.settings["stop_gap_ms"]),
            speed=self.app.settings["speed"],
            speed_method=self.app.settings["speed_method"],
            overrides=self.app.overrides,
            position_overrides=position_overrides,
        )

    def on_position_overrides(self):
        """Opens a dialog to adjust one specific OCCURRENCE of a syllable
        in the current text - e.g. only the second '가' in '가나가', not
        every '가' anywhere (that's what the 고급 설정 tab's per-file
        overrides already do). Session-only: never written to
        sound_overrides.json, and only meaningful for the exact text it
        was set up against (see _build)."""
        text = self.current_text()
        if not text:
            messagebox.showinfo("한국어 TTS", "먼저 읽을 문장을 입력하세요.")
            return
        settings_block = ktts._migrate_legacy_type(self.app.bank_manifest())
        phonology = ktts.resolve_phonology_options(settings_block)
        chars = ktts.syllable_position_chars(text, phonology=phonology)
        if not chars:
            messagebox.showinfo("한국어 TTS", "조정할 음절이 없습니다.")
            return
        if self._position_overrides_text != text:
            # Text changed since these were last set - stale indices, drop them.
            self.position_overrides = {}
        self._position_overrides_text = text
        self._open_position_dialog(chars, settings_block)

    def _open_position_dialog(self, chars, settings_block):
        win = tk.Toplevel(self)
        win.title("위치별 세부 조정")
        win.geometry("620x560")
        win.transient(self)

        ttk.Label(
            win, foreground="#555", wraplength=520,
            text="목록에서 음절을 골라 그 위치에만 적용될 조정을 합니다. "
                 "같은 글자가 문장 다른 곳에 또 나와도 영향 없습니다. "
                 "문장을 수정하면 이 조정은 다시 확인이 필요합니다 (저장되지 않음).",
        ).pack(anchor="w", padx=8, pady=(8, 4))

        body = ttk.Frame(win)
        body.pack(fill="both", expand=True, padx=8, pady=4)

        listbox = tk.Listbox(body, width=14, exportselection=False)
        listbox.pack(side="left", fill="y")
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=listbox.yview)
        scrollbar.pack(side="left", fill="y")
        listbox.config(yscrollcommand=scrollbar.set)

        def label_for(i):
            marker = " *" if i in self.position_overrides else ""
            return f"{i}: {chars[i]}{marker}"

        for i in range(len(chars)):
            listbox.insert("end", label_for(i))

        # The engine's own piece breakdown for THIS text (see
        # korean_tts.resolve_groups) - a CVC syllable is usually two pieces
        # (CV block + coda-tail, e.g. 녀+영 for 녕), so each piece gets its
        # own panel below with independent gain/trim, instead of one
        # setting applying identically to both halves.
        hex_pieces = bool(settings_block.get("hex_pieces"))
        naming = settings_block.get("naming", "hex-codepoint")
        sound_dir = self.app.current_sound_dir()
        real_groups = [g for g in ktts.resolve_groups(self._position_overrides_text, SOUND_ROOT,
                                                        self.app.settings["voice"])
                       if g[1] != [ktts.PAUSE]]
        raw_cache = {}
        audio_settings = self.app.audio_settings()

        def load_piece_raw(file_name):
            if file_name not in raw_cache:
                path = os.path.join(sound_dir, file_name + ".wav")
                raw_cache[file_name] = (
                    ktts.read_sample(path, audio_settings=audio_settings) if os.path.exists(path) else None
                )
            return raw_cache[file_name]

        detail = ttk.Frame(body)
        detail.pack(side="left", fill="both", expand=True, padx=(12, 0))

        # Scrollable so a 2-3 piece syllable's stacked panels (each with
        # gain/trim/crossfade/coda) never get clipped by the window height -
        # same pattern as voice_recorder.py's settings panel.
        piece_canvas = tk.Canvas(detail, highlightthickness=0)
        piece_scrollbar = ttk.Scrollbar(detail, orient="vertical", command=piece_canvas.yview)
        piece_canvas.configure(yscrollcommand=piece_scrollbar.set)
        piece_canvas.pack(side="top", fill="both", expand=True)
        piece_scrollbar.place(in_=piece_canvas, relx=1.0, rely=0, relheight=1.0, anchor="ne")
        piece_container = ttk.Frame(piece_canvas)
        piece_canvas.create_window((0, 0), window=piece_container, anchor="nw")
        piece_container.bind(
            "<Configure>", lambda _e: piece_canvas.configure(scrollregion=piece_canvas.bbox("all"))
        )
        piece_canvas.bind("<Enter>", lambda _e: piece_canvas.bind_all(
            "<MouseWheel>", lambda ev: piece_canvas.yview_scroll(int(-1 * (ev.delta / 120)), "units")))
        piece_canvas.bind("<Leave>", lambda _e: piece_canvas.unbind_all("<MouseWheel>"))

        stopgap_row = ttk.Frame(detail)
        stopgap_row.pack(fill="x", pady=(8, 2))
        stopgap_enabled = tk.BooleanVar(value=False)
        stopgap_var = tk.DoubleVar(value=ktts.DEFAULT_STOP_GAP_MS)
        stopgap_text = tk.StringVar()

        def sync_stopgap(_evt=None):
            stopgap_text.set(f"받침 ㄱㄷㅂ 뒤 간격: {int(stopgap_var.get())}ms")
            commit()

        ttk.Checkbutton(stopgap_row, variable=stopgap_enabled, command=sync_stopgap).pack(side="left")
        ttk.Label(stopgap_row, textvariable=stopgap_text, width=26).pack(side="left")
        ttk.Scale(stopgap_row, from_=0, to=200, orient="horizontal", variable=stopgap_var,
                  command=sync_stopgap).pack(side="left", fill="x", expand=True, padx=4)

        current_index = {"i": None}
        piece_vars = []  # rebuilt per selection: one dict of tk Vars per piece in that position
        stopgap_baseline = {"present": False, "value": ktts.DEFAULT_STOP_GAP_MS}

        def add_optional_row(parent, label, enabled_var, value_var, lo, hi, on_change):
            row = ttk.Frame(parent)
            row.pack(fill="x", padx=4, pady=2)
            text_var = tk.StringVar(value=f"{label}: {int(value_var.get())}ms")

            def sync(_evt=None):
                text_var.set(f"{label}: {int(value_var.get())}ms")
                on_change()

            ttk.Checkbutton(row, variable=enabled_var, command=sync).pack(side="left")
            ttk.Label(row, textvariable=text_var, width=26).pack(side="left")
            ttk.Scale(row, from_=lo, to=hi, orient="horizontal", variable=value_var,
                      command=sync).pack(side="left", fill="x", expand=True, padx=4)
            return sync

        def collect_piece(v):
            # Sliders are pre-filled from this piece's CURRENT 고급 설정
            # value (see build_piece_panels) so the user can see how it's
            # set before touching anything - so a field is only written
            # into the position override when it actually DIFFERS from
            # that baseline; left alone, it stays unwritten and keeps
            # inheriting the file-level setting live (including any LATER
            # change made in 고급 설정), instead of silently freezing
            # today's value into this one occurrence.
            b = v["baseline"]
            override = {}
            gain = round(v["gain_var"].get(), 1)
            if gain != round(b["gain_db"], 1):
                override["gain_db"] = gain
            start_ms = int(v["trim_start_var"].get())
            if start_ms != int(b["trim_start_ms"]):
                override["trim_start_ms"] = start_ms
            end_ms = int(v["trim_end_var"].get())
            if end_ms != int(b["trim_end_ms"]):
                override["trim_end_ms"] = end_ms
            if v["crossfade_enabled"] is not None:
                checked, value = v["crossfade_enabled"].get(), int(v["crossfade_var"].get())
                if checked and (not b["crossfade_present"] or value != int(b["crossfade_ms"])):
                    override["crossfade_ms"] = value
            if v["coda_enabled"] is not None:
                checked, value = v["coda_enabled"].get(), int(v["coda_var"].get())
                if checked and (not b["coda_present"] or value != int(b["coda_ms"])):
                    override["coda_max_ms"] = value
            return override

        def refresh_marker(i):
            listbox.delete(i)
            listbox.insert(i, label_for(i))
            listbox.selection_set(i)

        def commit():
            i = current_index["i"]
            if i is None or not piece_vars:
                return
            entries = [collect_piece(v) for v in piece_vars]
            checked, value = stopgap_enabled.get(), int(stopgap_var.get())
            if checked and (not stopgap_baseline["present"] or value != int(stopgap_baseline["value"])):
                entries[-1] = {**entries[-1], "stop_gap_ms": value}
            if any(entries):
                self.position_overrides[i] = entries
            else:
                self.position_overrides.pop(i, None)
            refresh_marker(i)

        def build_piece_panels(i):
            for child in piece_container.winfo_children():
                child.destroy()
            piece_vars.clear()

            kind, names = real_groups[i]
            file_names = [ktts.piece_filename(n, hex_pieces, naming) for n in names]
            stored = self.position_overrides.get(i) or []
            multi = len(names) > 1
            join_viz_widgets = {}  # join index (piece idx > 0) -> its CrossfadeVisualizer

            def effective_chunk(j):
                """This piece's samples exactly as real synthesis would use
                them right now: raw + its current (live, maybe-unsaved)
                gain/trim, then coda-shortened if it's a bare sonorant tail
                past the first piece - same preprocessing build_audio()
                applies before ever handing chunks to _crossfade_join."""
                st = piece_vars[j]
                raw = load_piece_raw(st["file_name"])
                if raw is None:
                    return array.array("h")
                chunk = ktts.apply_override(array.array("h", raw), {
                    "gain_db": st["gain_var"].get(),
                    "trim_start_ms": st["trim_start_var"].get(),
                    "trim_end_ms": st["trim_end_var"].get(),
                })
                if j > 0 and st["name"] in ktts.CODA_TAILS:
                    coda_enabled = st["coda_enabled"]
                    coda_ms = (st["coda_var"].get() if coda_enabled is not None and coda_enabled.get()
                               else st["baseline"]["coda_ms"])
                    chunk = ktts._shorten_coda(chunk, max_ms=coda_ms, fade_ms=audio_settings.coda_tail_fade_ms)
                return chunk

            def refresh_viz(j):
                """Redraw the join-j visualizer (between piece j-1 and j),
                if this position even has one at that index."""
                widget = join_viz_widgets.get(j)
                if widget is None:
                    return
                chunk_a, chunk_b = effective_chunk(j - 1), effective_chunk(j)
                if not len(chunk_a) or not len(chunk_b):
                    widget.clear()
                    return
                cf_enabled = piece_vars[j]["crossfade_enabled"]
                override_ms = int(piece_vars[j]["crossfade_var"].get()) \
                    if cf_enabled is not None and cf_enabled.get() else None
                combined = ktts._crossfade_join(
                    [chunk_a, chunk_b], kind, [piece_vars[j - 1]["file_name"], piece_vars[j]["file_name"]],
                    {piece_vars[j]["file_name"]: {"crossfade_ms": override_ms}} if override_ms is not None else {},
                    audio_settings=audio_settings,
                )
                _render_crossfade_join(widget, chunk_a, chunk_b, combined)

            for idx, (name, file_name) in enumerate(zip(names, file_names)):
                piece_override = stored[idx] if idx < len(stored) and stored[idx] else {}
                # The file's CURRENT 고급 설정 (per-sample tuning tab) value
                # for this piece - shown here as the starting point so the
                # user sees how it's already set before changing anything
                # for just this one occurrence (see collect_piece).
                file_override = self.app.overrides.get(file_name, {})
                baseline = {
                    "gain_db": file_override.get("gain_db", 0.0),
                    "trim_start_ms": file_override.get("trim_start_ms", 0.0),
                    "trim_end_ms": file_override.get("trim_end_ms", 0.0),
                    "crossfade_present": "crossfade_ms" in file_override,
                    "crossfade_ms": file_override.get("crossfade_ms", 60),
                    "coda_present": "coda_max_ms" in file_override,
                    "coda_ms": file_override.get("coda_max_ms", ktts.CODA_MAX_MS),
                }
                label_char = ktts.piece_display_char(name, naming)
                title = f"{'앞' if idx == 0 else '뒤'} 조각 - {label_char}" if multi else f"{label_char}"
                frame = ttk.LabelFrame(piece_container, text=title)
                frame.pack(fill="x", pady=4)

                raw = load_piece_raw(file_name)
                duration_ms = (len(raw) / ktts.TARGET_RATE * 1000) if raw else 0
                trim_hi = max(0, min(300, int(duration_ms))) if raw else 300

                gain_var = tk.DoubleVar(value=piece_override.get("gain_db", baseline["gain_db"]))
                gain_text = tk.StringVar()
                trim_start_var = tk.DoubleVar(
                    value=min(piece_override.get("trim_start_ms", baseline["trim_start_ms"]), trim_hi))
                trim_end_var = tk.DoubleVar(
                    value=min(piece_override.get("trim_end_ms", baseline["trim_end_ms"]), trim_hi))
                trim_start_text = tk.StringVar()
                trim_end_text = tk.StringVar()

                # Registered now (crossfade/coda refs filled in below once
                # built) so the join-visualizer for THIS piece's join can
                # already see the PREVIOUS piece's finalized state, and a
                # later piece's join-visualizer can see this one.
                piece_state = {
                    "name": name, "file_name": file_name, "baseline": baseline,
                    "gain_var": gain_var, "trim_start_var": trim_start_var, "trim_end_var": trim_end_var,
                    "crossfade_enabled": None, "crossfade_var": None,
                    "coda_enabled": None, "coda_var": None,
                }
                piece_vars.append(piece_state)

                ttk.Label(frame, text="파형 (드래그해서 자르기 구간 조절 - 지금 이 위치에 설정된 그대로)",
                          foreground="#888").pack(anchor="w", padx=4)
                waveform = WaveformEditor(frame, on_drag=lambda s, e: None)
                waveform.pack(padx=4, pady=(0, 4))
                if raw is not None:
                    waveform.load(raw)
                    waveform.set_trim(trim_start_var.get(), trim_end_var.get())
                else:
                    waveform.redraw()

                def sync(_evt=None, gv=gain_var, gt=gain_text, sv=trim_start_var, st=trim_start_text,
                         ev=trim_end_var, et=trim_end_text, wf=waveform, idx=idx):
                    gt.set(f"음량 보정: {gv.get():+.1f}dB")
                    st.set(f"시작 자르기: {int(sv.get())}ms")
                    et.set(f"끝 자르기: {int(ev.get())}ms")
                    wf.set_trim(sv.get(), ev.get())
                    commit()
                    # This piece's gain/trim feeds into both the join right
                    # before it and the join right after it.
                    refresh_viz(idx)
                    refresh_viz(idx + 1)

                def on_drag(start_ms, end_ms, sv=trim_start_var, ev=trim_end_var, s=sync):
                    sv.set(round(start_ms))
                    ev.set(round(end_ms))
                    s()

                waveform.on_drag = on_drag

                # Fill the labels directly instead of calling sync() here:
                # sync() also calls commit(), which reads ALL of piece_vars -
                # but this piece hasn't been appended to it yet (that only
                # happens once every widget below is built), so committing
                # now would see an incomplete piece list and could attach
                # stop_gap_ms to the wrong (not-yet-last) entry. Real
                # commits only need to happen from here on in response to
                # actual user interaction, once construction has finished.
                gain_text.set(f"음량 보정: {gain_var.get():+.1f}dB")
                trim_start_text.set(f"시작 자르기: {int(trim_start_var.get())}ms")
                trim_end_text.set(f"끝 자르기: {int(trim_end_var.get())}ms")
                ttk.Label(frame, textvariable=gain_text).pack(anchor="w", padx=4)
                ttk.Scale(frame, from_=-12, to=12, orient="horizontal", variable=gain_var,
                          command=sync).pack(fill="x", padx=4, pady=(0, 4))
                ttk.Label(frame, textvariable=trim_start_text).pack(anchor="w", padx=4)
                ttk.Scale(frame, from_=0, to=trim_hi, orient="horizontal", variable=trim_start_var,
                          command=sync).pack(fill="x", padx=4, pady=(0, 4))
                ttk.Label(frame, textvariable=trim_end_text).pack(anchor="w", padx=4)
                ttk.Scale(frame, from_=0, to=trim_hi, orient="horizontal", variable=trim_end_var,
                          command=sync).pack(fill="x", padx=4, pady=(0, 4))

                if idx > 0:
                    # Only a non-first piece is ever consulted for the join
                    # right before it (see korean_tts._crossfade_join: the
                    # LATER piece's crossfade_ms wins) - showing it here
                    # matches which piece actually controls that join.
                    # A key present in the stored position override is this
                    # position's own explicit choice; otherwise fall back to
                    # the file's current 고급 설정 value/checked-state - same
                    # per-key fallback build_audio()'s own merge uses.
                    cf_checked = ("crossfade_ms" in piece_override) or baseline["crossfade_present"]
                    cf_value = piece_override.get("crossfade_ms", baseline["crossfade_ms"])
                    crossfade_enabled = tk.BooleanVar(value=cf_checked)
                    crossfade_var = tk.DoubleVar(value=cf_value)
                    add_optional_row(frame, "앞 조각과 교차 길이", crossfade_enabled, crossfade_var, 0, 200,
                                      lambda idx=idx: (commit(), refresh_viz(idx)))
                    piece_state["crossfade_enabled"] = crossfade_enabled
                    piece_state["crossfade_var"] = crossfade_var

                    if name in ktts.CODA_TAILS:
                        coda_checked = ("coda_max_ms" in piece_override) or baseline["coda_present"]
                        coda_value = piece_override.get("coda_max_ms", baseline["coda_ms"])
                        coda_enabled = tk.BooleanVar(value=coda_checked)
                        coda_var = tk.DoubleVar(value=coda_value)
                        add_optional_row(frame, "받침 유지 길이", coda_enabled, coda_var, 0, 300,
                                          lambda idx=idx: (commit(), refresh_viz(idx)))
                        piece_state["coda_enabled"] = coda_enabled
                        piece_state["coda_var"] = coda_var

                    ttk.Label(frame, text="교차 시각화 (앞 조각과의 실제 결과)",
                              foreground="#888").pack(anchor="w", padx=4, pady=(6, 0))
                    join_viz = CrossfadeVisualizer(frame)
                    join_viz.pack(padx=4, pady=(0, 4))
                    join_viz_widgets[idx] = join_viz
                    refresh_viz(idx)

            # The stop-gap control is position-level (only the LAST piece's
            # value is ever consulted - see build_audio()), so its baseline
            # comes from that last piece's own 고급 설정 value, same idea as
            # every per-piece field above.
            last_file_override = self.app.overrides.get(file_names[-1], {})
            stopgap_baseline["present"] = "stop_gap_ms" in last_file_override
            stopgap_baseline["value"] = last_file_override.get("stop_gap_ms", ktts.DEFAULT_STOP_GAP_MS)
            last = stored[-1] if stored else {}
            stopgap_enabled.set(("stop_gap_ms" in last) or stopgap_baseline["present"])
            stopgap_var.set(last.get("stop_gap_ms", stopgap_baseline["value"]))
            sync_stopgap_label_only()

        def sync_stopgap_label_only():
            stopgap_text.set(f"받침 ㄱㄷㅂ 뒤 간격: {int(stopgap_var.get())}ms")

        def on_select(_evt=None):
            selection = listbox.curselection()
            if not selection:
                return
            i = selection[0]
            current_index["i"] = i
            build_piece_panels(i)

        listbox.bind("<<ListboxSelect>>", on_select)

        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=8, pady=8)

        def reset_this():
            i = current_index["i"]
            if i is None:
                return
            self.position_overrides.pop(i, None)
            refresh_marker(i)
            build_piece_panels(i)

        def reset_all():
            self.position_overrides.clear()
            for i in range(len(chars)):
                listbox.delete(i)
                listbox.insert(i, label_for(i))
            if current_index["i"] is not None:
                build_piece_panels(current_index["i"])

        ttk.Button(btns, text="이 위치 초기화", command=reset_this).pack(side="left")
        ttk.Button(btns, text="전체 초기화", command=reset_all).pack(side="left", padx=8)
        ttk.Button(btns, text="닫기", command=win.destroy).pack(side="right")

        listbox.selection_set(0)
        on_select()

    def on_play(self):
        text = self.current_text()
        if not text:
            return

        def work():
            try:
                track, missing = self._build(text)
            except ktts.AudioError as e:
                self.after(0, lambda: messagebox.showerror("한국어 TTS", f"오디오 오류: {e}"))
                return
            if not len(track):
                self.after(0, lambda: messagebox.showinfo("한국어 TTS", "읽을 수 있는 한글이 없습니다."))
                return
            wav_bytes = ktts.to_wav_bytes(track)
            volume = self.app.settings["volume"]
            if volume < 1.0:
                wav_bytes = _scale_wav_volume(wav_bytes, volume)
            self.app.player.play(wav_bytes)
            if missing:
                names = ", ".join(sorted(set(missing)))
                self.after(0, lambda: messagebox.showwarning("한국어 TTS", f"음성 조각을 찾지 못해 건너뜀: {names}"))

        threading.Thread(target=work, daemon=True).start()

    def on_save(self):
        text = self.current_text()
        if not text:
            return
        path = filedialog.asksaveasfilename(defaultextension=".wav", filetypes=[("WAV", "*.wav")])
        if not path:
            return

        def work():
            try:
                track, missing = self._build(text)
            except ktts.AudioError as e:
                self.after(0, lambda: messagebox.showerror("한국어 TTS", f"오디오 오류: {e}"))
                return
            if not len(track):
                self.after(0, lambda: messagebox.showinfo("한국어 TTS", "읽을 수 있는 한글이 없습니다."))
                return
            wav_bytes = ktts.to_wav_bytes(track)
            volume = self.app.settings["volume"]
            if volume < 1.0:
                wav_bytes = _scale_wav_volume(wav_bytes, volume)
            with open(path, "wb") as f:
                f.write(wav_bytes)
            self.after(0, lambda: messagebox.showinfo("한국어 TTS", f"저장됨: {path}"))

        threading.Thread(target=work, daemon=True).start()


def _scale_wav_volume(wav_bytes: bytes, volume: float) -> bytes:
    """Apply the volume slider (0..1) to a rendered WAV blob for playback/export."""
    with wave.open(io.BytesIO(wav_bytes)) as w:
        raw = w.readframes(w.getnframes())
    import array
    samples = array.array("h")
    samples.frombytes(raw)
    samples = array.array("h", (max(-32768, min(32767, int(x * volume))) for x in samples))
    return ktts.to_wav_bytes(samples)


# ------------------------------------------------------
# Fine-tuning tab
# ------------------------------------------------------

class WaveformEditor(tk.Canvas):
    """Visual trim editor: draws the sample's waveform with the trimmed-away
    start/end regions shaded out, and lets the user drag either edge marker
    directly instead of guessing millisecond values on a blind slider.
    """

    WIDTH = 460
    HEIGHT = 100
    MIN_GAP_MS = 20  # never let the two markers cross closer than this

    def __init__(self, parent, on_drag):
        super().__init__(parent, width=self.WIDTH, height=self.HEIGHT, background="#1e1e1e", highlightthickness=1,
                          highlightbackground="#888")
        self.on_drag = on_drag  # callback(start_ms, end_ms)
        self.peaks = []
        self.duration_ms = 0.0
        self.trim_start_ms = 0.0
        self.trim_end_ms = 0.0
        self._dragging = None
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_motion)

    def load(self, samples):
        n = len(samples)
        self.duration_ms = (n / ktts.TARGET_RATE) * 1000 if n else 0.0
        width = self.WIDTH
        peaks = []
        for x in range(width):
            start = n * x // width
            end = max(start + 1, n * (x + 1) // width)
            chunk = samples[start:end]
            peaks.append(max((abs(v) for v in chunk), default=0))
        self.peaks = peaks

    def set_trim(self, start_ms, end_ms):
        self.trim_start_ms = start_ms
        self.trim_end_ms = end_ms
        self.redraw()

    def redraw(self):
        self.delete("all")
        if not self.peaks or self.duration_ms <= 0:
            self.create_text(self.WIDTH // 2, self.HEIGHT // 2, text="(선택 없음)", fill="#888")
            return

        px_start = self._ms_to_px(self.trim_start_ms)
        px_end = self._ms_to_px(max(self.trim_start_ms, self.duration_ms - self.trim_end_ms))

        self.create_rectangle(0, 0, px_start, self.HEIGHT, fill="#3a1e1e", outline="")
        self.create_rectangle(px_end, 0, self.WIDTH, self.HEIGHT, fill="#3a1e1e", outline="")
        self.create_rectangle(px_start, 0, px_end, self.HEIGHT, fill="#1e2e1e", outline="")

        mid = self.HEIGHT // 2
        scale = (self.HEIGHT / 2 - 4) / 32768
        for x, peak in enumerate(self.peaks):
            h = peak * scale
            self.create_line(x, mid - h, x, mid + h, fill="#6fcf97")

        self.create_line(px_start, 0, px_start, self.HEIGHT, fill="#4d84ff", width=2)
        self.create_line(px_end, 0, px_end, self.HEIGHT, fill="#4d84ff", width=2)
        self.create_rectangle(px_start - 4, 0, px_start + 4, 10, fill="#4d84ff", outline="")
        self.create_rectangle(px_end - 4, 0, px_end + 4, 10, fill="#4d84ff", outline="")

    def _ms_to_px(self, ms):
        return max(0, min(self.WIDTH, (ms / self.duration_ms) * self.WIDTH)) if self.duration_ms else 0

    def _px_to_ms(self, px):
        return max(0.0, min(self.duration_ms, (px / self.WIDTH) * self.duration_ms))

    def _on_press(self, event):
        if not self.duration_ms:
            return
        px_start = self._ms_to_px(self.trim_start_ms)
        px_end = self._ms_to_px(self.duration_ms - self.trim_end_ms)
        self._dragging = "start" if abs(event.x - px_start) <= abs(event.x - px_end) else "end"
        self._on_motion(event)

    def _on_motion(self, event):
        if not self._dragging or not self.duration_ms:
            return
        ms = self._px_to_ms(event.x)
        if self._dragging == "start":
            max_start = max(0.0, self.duration_ms - self.trim_end_ms - self.MIN_GAP_MS)
            self.trim_start_ms = max(0.0, min(ms, max_start))
        else:
            end_ms = self.duration_ms - ms
            max_end = max(0.0, self.duration_ms - self.trim_start_ms - self.MIN_GAP_MS)
            self.trim_end_ms = max(0.0, min(end_ms, max_end))
        self.redraw()
        self.on_drag(self.trim_start_ms, self.trim_end_ms)


class CrossfadeVisualizer(tk.Canvas):
    """Read-only, zoomed-in view of one crossfade join: the REAL blended
    output (via korean_tts._crossfade_join, the exact function real
    synthesis uses) around the join boundary, with the overlap band
    shaded - so what's drawn is byte-for-byte what would actually play,
    not an abstract diagram. A thin margin of each side's still-unfaded
    audio is included around the shaded band for context.
    """

    WIDTH = 420
    HEIGHT = 80

    def __init__(self, parent):
        super().__init__(parent, width=self.WIDTH, height=self.HEIGHT, background="#1e1e1e",
                          highlightthickness=1, highlightbackground="#888")
        self.peaks = []
        self.overlap_start_frac = 0.0
        self.overlap_end_frac = 0.0
        self.overlap_ms = 0.0
        self.redraw()

    def set_data(self, window_samples, overlap_start_ms, overlap_end_ms, window_duration_ms):
        n = len(window_samples)
        width = self.WIDTH
        peaks = []
        for x in range(width):
            start = n * x // width
            end = max(start + 1, n * (x + 1) // width)
            chunk = window_samples[start:end]
            peaks.append(max((abs(v) for v in chunk), default=0))
        self.peaks = peaks
        self.overlap_start_frac = (overlap_start_ms / window_duration_ms) if window_duration_ms else 0.0
        self.overlap_end_frac = (overlap_end_ms / window_duration_ms) if window_duration_ms else 0.0
        self.overlap_ms = max(0.0, overlap_end_ms - overlap_start_ms)
        self.redraw()

    def clear(self, message="(교차 없음)"):
        self.peaks = []
        self._empty_message = message
        self.redraw()

    def redraw(self):
        self.delete("all")
        if not self.peaks:
            self.create_text(self.WIDTH // 2, self.HEIGHT // 2,
                              text=getattr(self, "_empty_message", "(선택 없음)"), fill="#888")
            return

        px_start = max(0, min(self.WIDTH, self.overlap_start_frac * self.WIDTH))
        px_end = max(0, min(self.WIDTH, self.overlap_end_frac * self.WIDTH))
        self.create_rectangle(px_start, 0, px_end, self.HEIGHT, fill="#2e2e55", outline="")

        mid = self.HEIGHT // 2
        scale = (self.HEIGHT / 2 - 4) / 32768
        for x, peak in enumerate(self.peaks):
            h = peak * scale
            self.create_line(x, mid - h, x, mid + h, fill="#6fcf97")

        self.create_line(px_start, 0, px_start, self.HEIGHT, fill="#8c8cff", width=1)
        self.create_line(px_end, 0, px_end, self.HEIGHT, fill="#8c8cff", width=1)
        self.create_text(self.WIDTH // 2, 10, text=f"교차 구간 {int(self.overlap_ms)}ms", fill="#cfe8ff")


def _render_crossfade_join(widget: CrossfadeVisualizer, chunk_a: array.array, chunk_b: array.array,
                            combined: array.array):
    """Feed a CrossfadeVisualizer the real output of joining chunk_a+chunk_b
    (as korean_tts._crossfade_join actually produced it, in `combined`) -
    zoomed to the overlap with a little context on each side. `combined`'s
    length is len(chunk_a)+len(chunk_b) minus however much they overlapped,
    which is all that's needed to locate the join without re-deriving the
    crossfade math here.
    """
    overlap = max(0, len(chunk_a) + len(chunk_b) - len(combined))
    join_index = len(chunk_a) - overlap
    margin = max(overlap // 2, int(ktts.TARGET_RATE * 30 / 1000))
    win_start = max(0, join_index - margin)
    win_end = min(len(combined), join_index + overlap + margin)
    window = combined[win_start:win_end]
    if not len(window):
        widget.clear()
        return
    window_duration_ms = len(window) / ktts.TARGET_RATE * 1000
    overlap_start_ms = (join_index - win_start) / ktts.TARGET_RATE * 1000
    overlap_end_ms = overlap_start_ms + (overlap / ktts.TARGET_RATE * 1000)
    widget.set_data(window, overlap_start_ms, overlap_end_ms, window_duration_ms)


class TuningTab(ttk.Frame):
    """Per-sample overrides, layered on top of the automatic trim+normalize
    pipeline (see korean_tts.apply_override / build_audio). Saved to
    config/sound_overrides.json (next to the .exe - see OVERRIDES_DIR),
    which tts.py/main.py/this GUI all read the same way, so a fix made here
    benefits every interface immediately.

    Beyond gain/trim, this also exposes the per-sample join-timing overrides
    build_audio understands (crossfade_ms / coda_max_ms / stop_gap_ms) via
    an enable checkbox next to each slider — unchecked means "don't write
    this key at all", so the automatic/global behavior still applies.
    """

    def __init__(self, parent, app: App):
        super().__init__(parent)
        self.app = app
        self.current_name = None
        self._raw_cache = {}  # name -> undamped samples (post auto-trim/normalize), for waveform + A/B preview

        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=8)
        self.voice_label = ttk.Label(top, text=f"목소리: {self.app.settings['voice']}", foreground="#555")
        self.voice_label.pack(side="left", padx=(0, 12))
        ttk.Label(top, text="검색:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._refresh_list())
        ttk.Entry(top, textvariable=self.search_var).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(top, text="내보내기...", command=self._export).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="가져오기...", command=self._import).pack(side="left", padx=(4, 0))
        ttk.Button(top, text="전체 초기화...", command=self._reset_all).pack(side="left", padx=(4, 0))

        # Whole-voice settings (bank.json's "audio" block) - unlike every
        # other slider in this tab, these apply to ALL of this voice's
        # samples regardless of which one is selected below, so they live
        # here rather than in the per-sample "선택한 음성 조각" panel.
        global_frame = ttk.LabelFrame(self, text="이 목소리 전체 설정")
        global_frame.pack(fill="x", padx=8, pady=(0, 4))
        self.global_crossfade_enabled = tk.BooleanVar(value=False)
        self.global_crossfade_var = tk.DoubleVar(value=150)
        self._add_optional_slider(
            global_frame, "전체 최대 교차 길이(ms) - 이 목소리의 모든 이음매에 적용",
            self.global_crossfade_enabled, self.global_crossfade_var, 20, 300,
            self._commit_global_crossfade,
        )

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=8, pady=4)

        list_frame = ttk.Frame(body)
        list_frame.pack(side="left", fill="both", expand=True)
        self.listbox = tk.Listbox(list_frame)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(list_frame, command=self.listbox.yview)
        scrollbar.pack(side="left", fill="y")
        self.listbox.config(yscrollcommand=scrollbar.set)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        # Scrollable so every control stays reachable regardless of window
        # height - this panel grew past a fixed 900x760 window's height once
        # the crossfade visualization was added (coda/stopgap sliders and
        # the buttons below them became invisible AND unclickable, since a
        # plain non-scrolling pack() just clips anything past the bottom
        # edge silently) - same canvas+scrollbar+inner-frame pattern
        # voice_recorder.py's settings panel already uses.
        detail_outer = ttk.LabelFrame(body, text="선택한 음성 조각")
        detail_outer.pack(side="left", fill="y", padx=(8, 0))
        detail_canvas = tk.Canvas(detail_outer, highlightthickness=0, width=500)
        detail_scrollbar = ttk.Scrollbar(detail_outer, orient="vertical", command=detail_canvas.yview)
        detail_canvas.configure(yscrollcommand=detail_scrollbar.set)
        detail_canvas.pack(side="left", fill="both", expand=True)
        detail_scrollbar.pack(side="left", fill="y")
        detail = ttk.Frame(detail_canvas)
        detail_canvas.create_window((0, 0), window=detail, anchor="nw")
        detail.bind("<Configure>", lambda _e: detail_canvas.configure(scrollregion=detail_canvas.bbox("all")))
        detail_canvas.bind("<Enter>", lambda _e: detail_canvas.bind_all(
            "<MouseWheel>", lambda ev: detail_canvas.yview_scroll(int(-1 * (ev.delta / 120)), "units")))
        detail_canvas.bind("<Leave>", lambda _e: detail_canvas.unbind_all("<MouseWheel>"))

        self.name_label = ttk.Label(detail, text="(선택 없음)", font=("", 11, "bold"))
        self.name_label.pack(anchor="w", padx=8, pady=8)

        ttk.Label(detail, text="파형 (드래그해서 자르기 구간 조절)").pack(anchor="w", padx=8)
        self.waveform = WaveformEditor(detail, on_drag=self._on_waveform_drag)
        self.waveform.pack(padx=8, pady=(0, 8))
        self.waveform.redraw()

        self.gain_var = tk.DoubleVar(value=0.0)
        self.gain_text = tk.StringVar(value="음량 보정: 0.0dB")
        ttk.Label(detail, textvariable=self.gain_text).pack(anchor="w", padx=8)
        ttk.Scale(detail, from_=-12, to=12, orient="horizontal", variable=self.gain_var,
                  command=self._on_gain_change, length=300).pack(padx=8, pady=4, fill="x")

        self.trim_start_var = tk.DoubleVar(value=0.0)
        self.trim_start_text = tk.StringVar(value="시작 자르기: 0ms")
        ttk.Label(detail, textvariable=self.trim_start_text).pack(anchor="w", padx=8)
        ttk.Scale(detail, from_=0, to=200, orient="horizontal", variable=self.trim_start_var,
                  command=self._on_trim_start_change, length=300).pack(padx=8, pady=4, fill="x")

        self.trim_end_var = tk.DoubleVar(value=0.0)
        self.trim_end_text = tk.StringVar(value="끝 자르기: 0ms")
        ttk.Label(detail, textvariable=self.trim_end_text).pack(anchor="w", padx=8)
        ttk.Scale(detail, from_=0, to=200, orient="horizontal", variable=self.trim_end_var,
                  command=self._on_trim_end_change, length=300).pack(padx=8, pady=4, fill="x")

        adv = ttk.LabelFrame(detail, text="고급: 이 조각이 관여하는 이음매 타이밍")
        adv.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Label(
            adv, foreground="#888", wraplength=380,
            text="이 세 설정은 다른 조각과 이어붙일 때만 효과가 있어서 "
                 "'▶ 조정본 미리듣기'(이 조각 하나만 재생)로는 절대 확인할 수 "
                 "없습니다 - 아래 '▶ 이음매 효과 미리듣기'를 쓰세요.",
        ).pack(anchor="w", padx=4, pady=(0, 4))

        self.crossfade_enabled = tk.BooleanVar(value=False)
        self.crossfade_var = tk.DoubleVar(value=60)
        self.crossfade_scale = self._add_optional_slider(
            adv, "교차 길이(ms) - 다음/이전 조각과의 겹침",
            self.crossfade_enabled, self.crossfade_var, 0, 200, self._commit)

        ttk.Label(adv, text="교차 시각화 - 이 조각을 다른 조각 뒤에 이어붙였을 때 (실제 결과)",
                  foreground="#888").pack(anchor="w", padx=4, pady=(4, 0))
        self.join_viz = CrossfadeVisualizer(adv)
        self.join_viz.pack(padx=4, pady=(0, 4))

        self.coda_enabled = tk.BooleanVar(value=False)
        self.coda_var = tk.DoubleVar(value=ktts.CODA_MAX_MS)
        self._add_optional_slider(adv, "받침 길이(ms) - ㄴㄹㅁㅇ 전용",
                                   self.coda_enabled, self.coda_var, 0, 300, self._commit)

        self.stopgap_enabled = tk.BooleanVar(value=False)
        self.stopgap_var = tk.DoubleVar(value=ktts.DEFAULT_STOP_GAP_MS)
        self._add_optional_slider(adv, "받침 뒤 간격(ms) - 이 조각이 ㄱㄷㅂ받침으로 끝날 때",
                                   self.stopgap_enabled, self.stopgap_var, 0, 300, self._commit)

        btns = ttk.Frame(detail)
        btns.pack(fill="x", padx=8, pady=8)
        ttk.Button(btns, text="▶ 조정본 미리듣기", command=self._preview).pack(side="left")
        ttk.Button(btns, text="▶ 원본 미리듣기", command=self._preview_original).pack(side="left", padx=8)
        ttk.Button(btns, text="▶ 이음매 효과 미리듣기", command=self._preview_join_timing).pack(side="left", padx=8)
        ttk.Button(btns, text="초기화", command=self._reset).pack(side="left", padx=8)
        ttk.Button(btns, text="실제 무음 자르기 (자동 감지)", command=self._suggest_smart_trim).pack(side="left", padx=8)

        ttk.Button(detail, text="모든 변경사항 저장", command=self._save_all).pack(anchor="w", padx=8, pady=(16, 8))
        self.status_label = ttk.Label(detail, text="", foreground="#888")
        self.status_label.pack(anchor="w", padx=8)

        self.all_names = self._list_sound_files()
        self._rebuild_search_keys()
        self._refresh_list()
        self._load_global_crossfade()

    def _load_global_crossfade(self):
        """Reflect the current voice's bank.json audio.crossfade_max_ms in
        the whole-voice slider - on (checked) only if it's already set as
        a single flat number (what this slider itself writes); a per-kind
        dict (hand-edited, or never touched) shows as off/default instead
        of guessing which of the three kinds' values to display."""
        value = (self.app.bank_manifest().get("audio") or {}).get("crossfade_max_ms")
        is_flat = isinstance(value, (int, float)) and not isinstance(value, bool)
        self.global_crossfade_enabled.set(is_flat)
        self.global_crossfade_var.set(value if is_flat else 150)

    def _commit_global_crossfade(self):
        value = int(self.global_crossfade_var.get()) if self.global_crossfade_enabled.get() else None
        self.app.set_bank_audio_setting("crossfade_max_ms", value)

    def _add_optional_slider(self, parent, label, enabled_var, value_var, lo, hi, on_change):
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=4, pady=2)
        text_var = tk.StringVar(value=f"{label}: {int(value_var.get())}ms")

        def sync(_evt=None):
            text_var.set(f"{label}: {int(value_var.get())}ms")
            on_change()

        ttk.Checkbutton(row, variable=enabled_var, command=sync).pack(side="left")
        ttk.Label(row, textvariable=text_var, wraplength=260).pack(side="left", padx=4)
        scale = ttk.Scale(row, from_=lo, to=hi, orient="horizontal", variable=value_var, command=sync, length=300)
        scale.pack(fill="x", padx=4, pady=(2, 4))
        return scale

    def _list_sound_files(self) -> list:
        sound_dir = self.app.current_sound_dir()
        if not os.path.isdir(sound_dir):
            return []
        return sorted(ktts.list_bank_files(sound_dir))

    def _refresh_list(self):
        query = self.search_var.get().strip().lower()
        self.listbox.delete(0, "end")
        # Matches by hex filename, romanized reading, OR the real Hangul
        # character (see korean_tts.search_key) - keyed once per voice
        # load (_rebuild_search_keys), not recomputed on every keystroke.
        self.filtered = [n for n in self.all_names if query in self._search_keys.get(n, n.lower())] \
            if query else self.all_names
        for name in self.filtered:
            marker = " *" if name in self.app.overrides else ""
            self.listbox.insert("end", name + marker)

    def _rebuild_search_keys(self):
        self._search_keys = {n: ktts.search_key(n) for n in self.all_names}

    def on_voice_changed(self):
        """Called by App.set_voice(): this tab's file list, cached raw
        samples, and current selection are all specific to the old voice."""
        self._raw_cache.clear()
        self.current_name = None
        self.name_label.config(text="(선택 없음)")
        self.voice_label.config(text=f"목소리: {self.app.settings['voice']}")
        self.waveform.peaks = []
        self.waveform.redraw()
        self.join_viz.clear()
        self.all_names = self._list_sound_files()
        self._rebuild_search_keys()
        self._refresh_list()
        self._load_global_crossfade()

    def _load_raw(self, name):
        if name not in self._raw_cache:
            path = os.path.join(self.app.current_sound_dir(), name + ".wav")
            self._raw_cache[name] = (
                ktts.read_sample(path, audio_settings=self.app.audio_settings())
                if os.path.exists(path) else None
            )
        return self._raw_cache[name]

    def _on_select(self, _evt=None):
        selection = self.listbox.curselection()
        if not selection:
            return
        name = self.filtered[selection[0]]
        self.current_name = name
        override = self.app.overrides.get(name, {})
        self.name_label.config(text=name)
        self.gain_var.set(override.get("gain_db", 0.0))
        self.trim_start_var.set(override.get("trim_start_ms", 0.0))
        self.trim_end_var.set(override.get("trim_end_ms", 0.0))

        self.crossfade_enabled.set("crossfade_ms" in override)
        self.coda_enabled.set("coda_max_ms" in override)
        self.coda_var.set(override.get("coda_max_ms", ktts.CODA_MAX_MS))
        self.stopgap_enabled.set("stop_gap_ms" in override)
        self.stopgap_var.set(override.get("stop_gap_ms", ktts.DEFAULT_STOP_GAP_MS))

        self._sync_labels()

        raw = self._load_raw(name)

        # Crossfade can never exceed this clip's own length anyway (build_
        # audio's _overlap_len already clamps to shorter-1), so cap the
        # slider itself at min(200ms, this file's duration) instead of
        # letting the user pick a value that's silently reduced later.
        duration_ms = (len(raw) / ktts.TARGET_RATE * 1000) if raw else 0
        crossfade_max = max(0, min(200, int(duration_ms)))
        self.crossfade_scale.config(to=crossfade_max)
        self.crossfade_var.set(min(override.get("crossfade_ms", 60), crossfade_max))

        if raw is not None:
            self.waveform.load(raw)
            self.waveform.set_trim(self.trim_start_var.get(), self.trim_end_var.get())
        else:
            self.waveform.peaks = []
            self.waveform.redraw()

        self._refresh_join_viz()

    def _on_waveform_drag(self, start_ms, end_ms):
        self.trim_start_var.set(round(start_ms))
        self.trim_end_var.set(round(end_ms))
        self._sync_labels()
        self._commit()

    def _sync_labels(self):
        self.gain_text.set(f"음량 보정: {self.gain_var.get():+.1f}dB")
        self.trim_start_text.set(f"시작 자르기: {int(self.trim_start_var.get())}ms")
        self.trim_end_text.set(f"끝 자르기: {int(self.trim_end_var.get())}ms")

    def _current_override(self) -> dict:
        override = {}
        gain = round(self.gain_var.get(), 1)
        if gain:
            override["gain_db"] = gain
        start_ms = int(self.trim_start_var.get())
        if start_ms:
            override["trim_start_ms"] = start_ms
        end_ms = int(self.trim_end_var.get())
        if end_ms:
            override["trim_end_ms"] = end_ms
        if self.crossfade_enabled.get():
            override["crossfade_ms"] = int(self.crossfade_var.get())
        if self.coda_enabled.get():
            override["coda_max_ms"] = int(self.coda_var.get())
        if self.stopgap_enabled.get():
            override["stop_gap_ms"] = int(self.stopgap_var.get())
        return override

    def _commit(self):
        if not self.current_name:
            return
        override = self._current_override()
        if override:
            self.app.overrides[self.current_name] = override
        else:
            self.app.overrides.pop(self.current_name, None)
        self._refresh_join_viz()

    def _refresh_join_viz(self):
        """Redraw self.join_viz with the REAL result of joining this
        sample after some other piece in the bank (same synthetic pairing
        _preview_join_timing() plays), using this sample's current
        (unsaved) gain/trim/crossfade sliders - so the visualization always
        reflects what you'd hear if you previewed right now, live as you
        drag any of those sliders."""
        if not self.current_name or not self.all_names:
            self.join_viz.clear()
            return
        name = self.current_name
        filler = next((n for n in self.all_names if n != name), None)
        if filler is None:
            self.join_viz.clear("(비교할 다른 조각 없음)")
            return
        filler_raw = self._load_raw(filler)
        name_raw = self._load_raw(name)
        if filler_raw is None or name_raw is None:
            self.join_viz.clear("(파일 없음)")
            return
        override = self._current_override()
        chunk_a = array.array("h", filler_raw)
        chunk_b = ktts.apply_override(array.array("h", name_raw), override)
        override_ms = override.get("crossfade_ms")
        combined = ktts._crossfade_join(
            [chunk_a, chunk_b], "coda", [filler, name],
            {name: {"crossfade_ms": override_ms}} if override_ms is not None else {},
            audio_settings=self.app.audio_settings(),
        )
        _render_crossfade_join(self.join_viz, chunk_a, chunk_b, combined)

    def _on_gain_change(self, _v):
        self._sync_labels()
        self._commit()

    def _on_trim_start_change(self, _v):
        self._sync_labels()
        self._commit()
        self.waveform.set_trim(self.trim_start_var.get(), self.trim_end_var.get())

    def _on_trim_end_change(self, _v):
        self._sync_labels()
        self._commit()
        self.waveform.set_trim(self.trim_start_var.get(), self.trim_end_var.get())

    def _suggest_smart_trim(self):
        """Fills trim_start_ms/trim_end_ms with korean_tts.smart_trim_bounds()'s
        suggestion for the selected sample - a windowed-RMS re-check that
        catches a quiet-but-not-silent tail/head (room tone, breath) the
        automatic per-sample trim_silence() left in place because a few
        individual samples in there poke just above the threshold (see
        korean_tts.trim_silence_smart's docstring for the real case this
        was built for). Only fills the sliders - like every other override
        here, nothing is written until '모든 변경사항 저장', so previewing
        first is always possible before committing to it."""
        if not self.current_name:
            return
        raw = self._load_raw(self.current_name)
        if raw is None:
            messagebox.showinfo("한국어 TTS", "이 항목은 아직 녹음되지 않았습니다.")
            return
        start, end = ktts.smart_trim_bounds(raw)
        start_ms = min(200, round(start / ktts.TARGET_RATE * 1000))
        end_ms = min(200, round((len(raw) - end) / ktts.TARGET_RATE * 1000))
        self.trim_start_var.set(start_ms)
        self.trim_end_var.set(end_ms)
        self._sync_labels()
        self._commit()
        self.waveform.set_trim(self.trim_start_var.get(), self.trim_end_var.get())
        self.status_label.config(
            text=f"'{self.current_name}': 실제 무음 감지 - 시작 {start_ms}ms / 끝 {end_ms}ms 자르기 제안 "
                 "(미리듣기로 확인 후 저장하세요)"
        )

    def _reset(self):
        if not self.current_name:
            return
        self.gain_var.set(0.0)
        self.trim_start_var.set(0.0)
        self.trim_end_var.set(0.0)
        self.crossfade_enabled.set(False)
        self.coda_enabled.set(False)
        self.stopgap_enabled.set(False)
        self._sync_labels()
        self.waveform.set_trim(0.0, 0.0)
        self.app.overrides.pop(self.current_name, None)
        self._refresh_list()
        self._refresh_join_viz()

    def _reset_all(self):
        """Batch version of 초기화: clears EVERY per-sample override for the
        CURRENT voice at once, not just the selected one - e.g. after
        VoiceRecorder.exe's whole-voice 정규화/무음 자르기 fixes the
        underlying audio itself, old manual overrides tuned against the
        pre-fix files may no longer make sense and are easiest to just
        start over from. Confirms first (shows how many will be cleared)
        since this can erase a lot of tuning work in one click. Like every
        other edit in this tab, only the in-memory dict is cleared -
        nothing is written to sound_overrides.json until '모든 변경사항
        저장', so this is safe to undo by simply not saving (or by
        reloading the voice, which re-reads the file untouched)."""
        count = len(self.app.overrides)
        if not count:
            messagebox.showinfo("한국어 TTS", "초기화할 조정이 없습니다.")
            return
        if not messagebox.askyesno(
            "한국어 TTS",
            f"이 목소리({self.app.settings['voice']})의 조각별 조정 {count}개를 모두 초기화합니다.\n"
            "'모든 변경사항 저장'을 눌러야 실제 파일에 반영됩니다. 계속할까요?",
        ):
            return
        self.app.overrides.clear()
        self._refresh_list()
        if self.current_name:
            self._on_select_by_name(self.current_name)
        self.status_label.config(text=f"{count}개 조정을 초기화했습니다 (저장하려면 '모든 변경사항 저장').")

    def _preview(self):
        if not self.current_name:
            return
        name = self.current_name
        override = self._current_override()

        def work():
            path = os.path.join(self.app.current_sound_dir(), name + ".wav")
            if not os.path.exists(path):
                self.after(0, lambda: messagebox.showwarning("한국어 TTS", f"파일이 없습니다: {name}.wav"))
                return
            samples = ktts.read_sample(path, override=override, audio_settings=self.app.audio_settings())
            wav_bytes = ktts.to_wav_bytes(samples)
            self.app.player.play(wav_bytes)

        threading.Thread(target=work, daemon=True).start()

    def _preview_join_timing(self):
        """Preview mode specifically for crossfade_ms/coda_max_ms/stop_gap_ms.
        _preview() plays this ONE sample in isolation, so those three
        settings - which only ever act BETWEEN pieces during real assembly
        (see build_audio) - have nothing to apply to there and can never
        audibly change anything, even though they work correctly in real
        playback (this is the exact confusion that prompted adding this:
        someone changing 받침 뒤 간격 here, hearing no difference via
        '조정본 미리듣기', and concluding the override didn't work, when
        changing the same setting globally in 재생 worked fine).

        Builds a small synthetic two-group sequence - [filler, this
        sample] as one "coda"-kind join, then a second lone group - so
        there's an adjacent piece for crossfade_ms to blend with and a
        following group for stop_gap_ms to appear before. The filler's
        own identity doesn't matter (any other sample already in this
        bank works); coda_max_ms only ever does anything if this sample's
        LOGICAL name is a bare-tail piece (n/l/m/ng) and stop_gap_ms only
        ever does anything if it logically ends in a stop coda, so this
        is harmless for a sample where neither applies - it just plays
        the ordinary crossfade-less "coda" join instead.

        A hex_pieces bank's on-disk name isn't the logical name these
        checks key off (see korean_tts.HEX_TO_ROMANIZED_NAME) - reversed
        here so the demo exercises the right logic regardless of naming.
        """
        if not self.current_name or not self.all_names:
            return
        name = self.current_name
        override = self._current_override()
        filler = next((n for n in self.all_names if n != name), name)

        settings = ktts._migrate_legacy_type(self.app.bank_manifest())
        hex_pieces = bool(settings.get("hex_pieces"))
        naming = settings.get("naming", "hex-codepoint")
        logical_name = ktts.HEX_TO_ROMANIZED_NAME.get(name, name) if hex_pieces else name
        logical_filler = ktts.HEX_TO_ROMANIZED_NAME.get(filler, filler) if hex_pieces else filler

        sound_dir = self.app.current_sound_dir()
        audio_settings = self.app.audio_settings()

        def work():
            groups = [("coda", [logical_filler, logical_name]), ("single", [logical_filler])]
            samples, missing = ktts.build_audio(
                groups, sound_dir, overrides={name: override} if override else None,
                audio_settings=audio_settings, hex_pieces=hex_pieces, naming=naming,
            )
            if missing:
                self.after(0, lambda: messagebox.showwarning(
                    "한국어 TTS", f"파일이 없습니다: {', '.join(missing)}"))
                return
            wav_bytes = ktts.to_wav_bytes(samples)
            self.app.player.play(wav_bytes)

        threading.Thread(target=work, daemon=True).start()

    def _preview_original(self):
        """A/B reference: plays the sample exactly as the automatic pipeline
        produces it, with no fine-tuning override applied, for comparison."""
        if not self.current_name:
            return
        name = self.current_name

        def work():
            raw = self._load_raw(name)
            if raw is None:
                self.after(0, lambda: messagebox.showwarning("한국어 TTS", f"파일이 없습니다: {name}.wav"))
                return
            wav_bytes = ktts.to_wav_bytes(raw)
            self.app.player.play(wav_bytes)

        threading.Thread(target=work, daemon=True).start()

    def _export(self):
        self._commit()
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")],
                                             initialfile="sound_overrides.json")
        if not path:
            return
        try:
            ktts.save_overrides(path, self.app.overrides)
        except OSError as e:
            messagebox.showerror("한국어 TTS", f"내보내기 실패: {e}")
            return
        messagebox.showinfo("한국어 TTS", f"내보냄: {path}")

    def _import(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("모든 파일", "*.*")])
        if not path:
            return
        imported = ktts.load_overrides(path)
        if not imported:
            messagebox.showwarning("한국어 TTS", "불러올 내용이 없습니다 (빈 파일이거나 형식이 올바르지 않습니다).")
            return
        merge = messagebox.askyesno(
            "한국어 TTS",
            f"{len(imported)}개 조각의 설정을 불러왔습니다.\n"
            "예: 기존 설정에 병합 (같은 이름은 덮어씀)\n"
            "아니오: 기존 설정을 전부 대체",
        )
        if merge:
            self.app.overrides.update(imported)
        else:
            self.app.overrides = imported
        self._refresh_list()
        if self.current_name:
            self._on_select_by_name(self.current_name)
        self.status_label.config(text="불러옴 (저장하려면 '모든 변경사항 저장'을 누르세요)")

    def _on_select_by_name(self, name):
        if name in self.filtered:
            idx = self.filtered.index(name)
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(idx)
            self._on_select()

    def _save_all(self):
        self._commit()
        path = self.app.overrides_path()
        try:
            ktts.save_overrides(path, self.app.overrides)
        except OSError as e:
            messagebox.showerror("한국어 TTS", f"저장 실패: {e}")
            return
        self._refresh_list()
        self.status_label.config(text=f"저장됨: {path}")


class BatchTab(ttk.Frame):
    """Turns a list of lines (typed in, or loaded from a .txt file) into one
    WAV file per line in a chosen output folder - a script/subtitle file in,
    a folder of numbered clips out. Uses the exact same settings/overrides
    as the 재생 tab (MainTab._build), so output matches what Play would
    produce for each line."""

    def __init__(self, parent, app: App):
        super().__init__(parent)
        self.app = app
        self.output_dir = None

        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=8)
        ttk.Label(top, text="한 줄에 한 문장씩 입력하거나 텍스트 파일을 불러오세요:").pack(anchor="w")
        ttk.Button(top, text="텍스트 파일 불러오기...", command=self._load_file).pack(anchor="w", pady=4)

        self.text_box = tk.Text(self, height=14, wrap="word")
        self.text_box.pack(fill="both", expand=True, padx=8, pady=4)

        out_row = ttk.Frame(self)
        out_row.pack(fill="x", padx=8, pady=4)
        ttk.Button(out_row, text="출력 폴더 선택...", command=self._choose_output_dir).pack(side="left")
        self.output_label = ttk.Label(out_row, text="(출력 폴더를 선택하세요)", foreground="#888")
        self.output_label.pack(side="left", padx=8)

        run_row = ttk.Frame(self)
        run_row.pack(fill="x", padx=8, pady=8)
        self.run_button = ttk.Button(run_row, text="전체 변환", command=self._run)
        self.run_button.pack(side="left")
        self.progress_label = ttk.Label(run_row, text="", foreground="#888")
        self.progress_label.pack(side="left", padx=8)

        note = ("설정(속도/간격/볼륨/받침 뒤 간격)과 고급 설정 탭의 조각별 미세조정은 "
                "재생 탭과 동일하게 적용됩니다. 파일명은 001_문장.wav 형식으로 저장됩니다.")
        ttk.Label(self, text=note, wraplength=800, foreground="#888").pack(anchor="w", padx=8, pady=(0, 8))

    def _load_file(self):
        path = filedialog.askopenfilename(filetypes=[("텍스트 파일", "*.txt"), ("모든 파일", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as e:
            messagebox.showerror("한국어 TTS", f"파일을 읽을 수 없습니다: {e}")
            return
        self.text_box.delete("1.0", "end")
        self.text_box.insert("1.0", content)

    def _choose_output_dir(self):
        path = filedialog.askdirectory()
        if not path:
            return
        self.output_dir = path
        self.output_label.config(text=path)

    def _lines(self):
        raw = self.text_box.get("1.0", "end")
        return [line.strip() for line in raw.splitlines() if line.strip()]

    @staticmethod
    def _sanitize_filename(text: str, max_len: int = 24) -> str:
        cleaned = "".join(c for c in text if c not in '\\/:*?"<>|').strip()
        cleaned = cleaned[:max_len] if cleaned else "untitled"
        return cleaned

    def _run(self):
        lines = self._lines()
        if not lines:
            messagebox.showinfo("한국어 TTS", "변환할 문장이 없습니다.")
            return
        if not self.output_dir:
            messagebox.showinfo("한국어 TTS", "먼저 출력 폴더를 선택하세요.")
            return

        self.run_button.config(state="disabled")

        def work():
            total = len(lines)
            all_missing = set()
            errors = []
            digits = len(str(total))
            for i, line in enumerate(lines, start=1):
                self.after(0, lambda i=i, total=total: self.progress_label.config(text=f"{i}/{total} 처리 중..."))
                try:
                    track, missing = self.app.main_tab._build(line)
                except ktts.AudioError as e:
                    errors.append(f"{line}: {e}")
                    continue
                if missing:
                    all_missing.update(missing)
                if not len(track):
                    errors.append(f"{line}: 읽을 수 있는 한글이 없습니다.")
                    continue
                wav_bytes = ktts.to_wav_bytes(track)
                volume = self.app.settings["volume"]
                if volume < 1.0:
                    wav_bytes = _scale_wav_volume(wav_bytes, volume)
                filename = f"{i:0{digits}d}_{self._sanitize_filename(line)}.wav"
                out_path = os.path.join(self.output_dir, filename)
                try:
                    with open(out_path, "wb") as f:
                        f.write(wav_bytes)
                except OSError as e:
                    errors.append(f"{line}: {e}")

            def done():
                self.run_button.config(state="normal")
                self.progress_label.config(text=f"완료: {total - len(errors)}/{total}")
                summary = [f"{total - len(errors)}개 중 {total}개 변환 완료.", f"저장 위치: {self.output_dir}"]
                if all_missing:
                    summary.append("음성 조각을 찾지 못해 건너뜀: " + ", ".join(sorted(all_missing)))
                if errors:
                    summary.append("오류:\n" + "\n".join(errors[:10]))
                messagebox.showinfo("한국어 TTS", "\n\n".join(summary))

            self.after(0, done)

        threading.Thread(target=work, daemon=True).start()


if __name__ == "__main__":
    if not os.path.isdir(SOUND_ROOT):
        print(f"음성 폴더가 없습니다: {SOUND_ROOT}")
        sys.exit(1)
    App().mainloop()
