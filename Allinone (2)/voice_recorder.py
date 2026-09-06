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


def _full_syllable_names(settings: dict) -> list:
    """Ordered hex-codepoint (or raw-Hangul, per settings) filenames for
    every REACHABLE composed Hangul syllable (korean_tts.
    all_reachable_full_syllables()) - not the raw 11,172, since syllables
    like 쟈 always resolve to 자's filename before a lookup ever happens
    (see korean_tts.py's _apply_local_vowel_rules) and recording them would
    be pure wasted effort. Composed-Hangul codepoints are already a
    mixed-radix encoding of (cho, jung, jong) - HANGUL_START + (cho*21 +
    jung)*28 + jong - so plain ascending codepoint order (what sorted()
    naturally gives here) already groups by onset+vowel first, no separate
    sort/grouping step needed for a speaker to stay in a similar mouth
    position through a batch."""
    naming = settings.get("naming", "hex-codepoint")
    preserve_ui = bool(settings.get("preserve_consonant_ui"))
    chars = sorted(ktts.all_reachable_full_syllables(preserve_ui))
    return [ktts.syllable_filename(ch, naming) for ch in chars]


def _full_syllable_prompts(settings: dict) -> dict:
    naming = settings.get("naming", "hex-codepoint")
    preserve_ui = bool(settings.get("preserve_consonant_ui"))
    chars = ktts.all_reachable_full_syllables(preserve_ui)
    return {ktts.syllable_filename(ch, naming): ch for ch in chars}


def _diphone_names(settings: dict) -> list:
    """Merged, ordered name list for the "diphone" bank type: every CV
    block (onset+nucleus) then every coda tail (nucleus+coda), each in
    ascending-codepoint order - see korean_tts.all_diphone_cv_blocks/
    all_diphone_coda_tails. One flat list needs no new UI: App already
    treats names/prompts generically regardless of what they represent."""
    naming = settings.get("naming", "hex-codepoint")
    preserve_ui = bool(settings.get("preserve_consonant_ui"))
    chars = sorted(ktts.all_diphone_cv_blocks(preserve_ui)) + sorted(ktts.all_diphone_coda_tails())
    return [ktts.syllable_filename(ch, naming) for ch in chars]


def _diphone_prompts(settings: dict) -> dict:
    naming = settings.get("naming", "hex-codepoint")
    preserve_ui = bool(settings.get("preserve_consonant_ui"))
    chars = ktts.all_diphone_cv_blocks(preserve_ui) | ktts.all_diphone_coda_tails()
    return {ktts.syllable_filename(ch, naming): ch for ch in chars}


# Per-bank-type (names, prompts) sources - see korean_tts.py's "Sound banks"
# section. App only ever needs these two things per type (confirmed while
# designing this: every other part of App - mic capture, progress/resume,
# record/preview/accept/save mechanics, the whole UI shell - already works
# on any (names, prompts) pair with no further changes).
BANK_TYPES = [
    {
        "type": ktts.BANK_TYPE_PIECES,
        "label": "조각 방식 (기본)",
        "names": lambda settings: sorted(ktts.all_reachable_samples()),
        "prompts": lambda settings: build_prompt_map(),
    },
    {
        "type": ktts.BANK_TYPE_FULL_SYLLABLE,
        "label": "완전한 음절 통째로",
        "names": _full_syllable_names,
        "prompts": _full_syllable_prompts,
    },
    {
        "type": ktts.BANK_TYPE_DIPHONE,
        "label": "온셋+중성 · 중성+받침 조각 (디폰)",
        "names": _diphone_names,
        "prompts": _diphone_prompts,
    },
]
BANK_TYPE_BY_ID = {t["type"]: t for t in BANK_TYPES}

# Optional per-type settings, exposed as checkboxes in the new-bank creation
# panel instead of requiring hand-editing bank.json afterward. The primary
# type choice above stays a plain required dropdown - this is deliberately a
# separate, secondary registry (settings WITHIN a type, not alternate types),
# and deliberately just {key, label} dicts so a future option is one entry
# away without touching _prepare_new_bank_ui/_create_new_bank again.
ADVANCED_OPTIONS = {
    ktts.BANK_TYPE_PIECES: [
        {"key": "dedicated_diphthongs", "label": "이중모음 별도 녹음"},
        {"key": "syllable_overrides", "label": "특정 음절 통째로 대체 녹음 허용"},
    ],
    ktts.BANK_TYPE_FULL_SYLLABLE: [
        {"key": "preserve_consonant_ui", "label": "자음+ㅢ 구분하여 녹음 (예: 씌)"},
    ],
    ktts.BANK_TYPE_DIPHONE: [
        {"key": "preserve_consonant_ui", "label": "자음+ㅢ 구분하여 녹음 (예: 씌)"},
    ],
}


def _peak_level(pcm: bytes) -> int:
    """Max absolute 16-bit sample value in `pcm` - a quick way to tell a
    real recording from near-silence (muted mic, wrong input device, or
    Windows blocking this app's microphone access at the OS level)."""
    import array

    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    return max((abs(s) for s in samples), default=0)


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
        self.geometry("760x640")

        self.prompts = {}
        self.names = []
        self.index = 0
        self.voice = tk.StringVar(value="")
        self.bank_type_var = tk.StringVar(value=BANK_TYPES[0]["label"])
        self.fallback_var = tk.StringVar(value="")
        self._pending_new_name = None
        self.advanced_vars = {}  # {settings key: tk.BooleanVar}, rebuilt per selected type
        self.override_char_var = tk.StringVar(value="")
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

        # New-bank type picker: hidden (not packed) until _load_voice() finds
        # the typed name doesn't exist yet - shown once per new bank, never
        # again once it's created (type is immutable after creation, since
        # changing it would orphan already-recorded files under the old
        # naming scheme).
        self.new_bank_frame = ttk.LabelFrame(self, text="새 목소리 만들기")
        ttk.Label(self.new_bank_frame, text="녹음 방식:").grid(row=0, column=0, padx=8, pady=6, sticky="w")
        self.type_combo = ttk.Combobox(
            self.new_bank_frame, textvariable=self.bank_type_var, state="readonly", width=20,
            values=[t["label"] for t in BANK_TYPES],
        )
        self.type_combo.current(0)
        self.type_combo.grid(row=0, column=1, padx=8, pady=6, sticky="w")
        self.type_combo.bind("<<ComboboxSelected>>", lambda _e: self._update_new_bank_fields())

        self.fallback_label = ttk.Label(self.new_bank_frame, text="빠진 음절 대체 목소리:")
        self.fallback_label.grid(row=1, column=0, padx=8, pady=6, sticky="w")
        self.fallback_combo = ttk.Combobox(self.new_bank_frame, textvariable=self.fallback_var, state="disabled", width=20)
        self.fallback_combo.grid(row=1, column=1, padx=8, pady=6, sticky="w")

        ttk.Button(self.new_bank_frame, text="만들기", command=self._create_new_bank).grid(
            row=0, column=2, rowspan=2, padx=12
        )
        # Rebuilt per selected type by _update_new_bank_fields() (see
        # ADVANCED_OPTIONS) - checkboxes for optional per-type settings, kept
        # separate from the type picker above so the basic "pick a type and
        # click 만들기" path never has to look at this. All default unchecked.
        self.advanced_frame = ttk.Frame(self.new_bank_frame)
        self.advanced_frame.grid(row=2, column=0, columnspan=3, padx=8, pady=(0, 6), sticky="w")
        # Not packed here - _prepare_new_bank_ui() packs it, _load_voice()/
        # _create_new_bank() pack_forget() it once a bank is open.

        # Patch-a-syllable panel for an already-open "pieces" bank (see
        # korean_tts.py's syllable_overrides setting): an open-ended "fix the
        # syllable that sounds wrong" workflow, not a fixed walkthrough list,
        # so it's deliberately NOT a BANK_TYPES registry entry - just an
        # additive panel shown/hidden by _open_bank(). Reuses the SAME
        # record/preview controls above (they don't reference a name at all,
        # only self.current_take) - only the save destination differs.
        self.override_frame = ttk.LabelFrame(self, text="특정 음절 다시 녹음 (전체 대체)")
        ttk.Label(self.override_frame, text="음절:").grid(row=0, column=0, padx=8, pady=6, sticky="w")
        override_entry = ttk.Entry(self.override_frame, textvariable=self.override_char_var, width=4, font=("", 16))
        override_entry.grid(row=0, column=1, padx=4, pady=6, sticky="w")
        self.override_char_var.trace_add("write", lambda *_a: self._update_override_status())
        self.override_status_label = ttk.Label(self.override_frame, text="", foreground="#888")
        self.override_status_label.grid(row=0, column=2, padx=8, pady=6, sticky="w")
        ttk.Button(self.override_frame, text="이 음절로 저장", command=self._accept_override).grid(
            row=0, column=3, padx=8, pady=6
        )
        ttk.Label(
            self.override_frame,
            text=("위의 ● 녹음 시작 / ▶ 들어보기로 녹음한 뒤 여기 '이 음절로 저장'을 누르면, 지금 목록에서 "
                  "보고 있는 항목과 별개로 이 글자 전체를 통째로 대체하는 녹음이 저장됩니다 (특정 음절만 "
                  "다시 녹음하고 싶을 때 사용)."),
            wraplength=560, foreground="#888",
        ).grid(row=1, column=0, columnspan=4, padx=8, pady=(0, 6), sticky="w")
        # Not packed here - _open_bank() packs/hides it based on bank type.

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
        # Populated once a bank is opened (_open_bank -> _refresh_done_markers) -
        # self.names is empty until then, since which items exist depends on
        # the bank's type.

        main = ttk.Frame(body)
        main.pack(side="left", fill="both", expand=True, padx=16)

        self.progress_label = ttk.Label(main, text="", foreground="#555")
        self.progress_label.pack(anchor="w")

        self.prompt_label = tk.Label(main, text="", font=("", 72, "bold"))
        self.prompt_label.pack(pady=(20, 4))

        self.name_label = ttk.Label(main, text="", foreground="#888")
        self.name_label.pack()

        self.status_label = ttk.Label(main, text="", foreground="#0a0", wraplength=440, justify="left")
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
        self.voice_combo["values"] = ktts.list_voices(SOUND_ROOT)

    # ------------------------------------------------------
    # Voice / navigation
    # ------------------------------------------------------

    def _load_voice(self):
        name = self.voice.get().strip()
        if not name:
            messagebox.showinfo("한국어 TTS", "목소리 이름을 입력하세요 (예: narrator2, 또는 기존 목소리를 고쳐 쓰려면 default).")
            return
        invalid = set(name) & set('\\/:*?"<>| ')
        if name.startswith(("_", ".")) or invalid:
            messagebox.showwarning(
                "한국어 TTS",
                "목소리 이름은 밑줄(_)/마침표(.)로 시작할 수 없고 공백이나 \\/:*?\"<>| 를 "
                "쓸 수 없습니다 (안드로이드가 그런 assets 폴더를 자동으로 무시합니다).",
            )
            return

        if os.path.isdir(os.path.join(SOUND_ROOT, name)):
            self.new_bank_frame.pack_forget()
            self._open_bank(name)
        else:
            self.override_frame.pack_forget()
            self._prepare_new_bank_ui(name)

    def _prepare_new_bank_ui(self, name):
        """A brand-new voice name: show the type (+ fallback-bank, if
        full-syllable) picker instead of creating the folder right away."""
        self._pending_new_name = name
        fallback_choices = [
            b["name"] for b in ktts.list_banks(SOUND_ROOT)
            if b["manifest"].get("type", ktts.BANK_TYPE_PIECES) == ktts.BANK_TYPE_PIECES
        ]
        self.fallback_combo["values"] = fallback_choices
        if ktts.DEFAULT_VOICE in fallback_choices:
            self.fallback_var.set(ktts.DEFAULT_VOICE)
        elif fallback_choices:
            self.fallback_var.set(fallback_choices[0])
        else:
            self.fallback_var.set("")
        self.type_combo.current(0)
        self._update_new_bank_fields()
        self.new_bank_frame.pack(fill="x", padx=10, pady=(0, 8))

    def _selected_bank_type(self) -> str:
        label = self.bank_type_var.get()
        for t in BANK_TYPES:
            if t["label"] == label:
                return t["type"]
        return ktts.BANK_TYPE_PIECES

    def _update_new_bank_fields(self):
        bank_type = self._selected_bank_type()
        needs_fallback = bank_type in (ktts.BANK_TYPE_FULL_SYLLABLE, ktts.BANK_TYPE_DIPHONE)
        self.fallback_combo.config(state="readonly" if needs_fallback else "disabled")

        for child in self.advanced_frame.winfo_children():
            child.destroy()
        self.advanced_vars = {}
        options = ADVANCED_OPTIONS.get(bank_type, [])
        if options:
            ttk.Label(self.advanced_frame, text="고급 설정:", foreground="#555").grid(
                row=0, column=0, sticky="w", pady=(4, 0)
            )
            for i, opt in enumerate(options):
                var = tk.BooleanVar(value=False)
                self.advanced_vars[opt["key"]] = var
                ttk.Checkbutton(self.advanced_frame, text=opt["label"], variable=var).grid(
                    row=i + 1, column=0, sticky="w"
                )

    def _create_new_bank(self):
        name = self._pending_new_name
        if not name:
            return
        bank_type = self._selected_bank_type()
        settings = {key: True for key, var in self.advanced_vars.items() if var.get()}
        if bank_type in (ktts.BANK_TYPE_FULL_SYLLABLE, ktts.BANK_TYPE_DIPHONE):
            fallback = self.fallback_var.get().strip()
            if not fallback:
                messagebox.showwarning(
                    "한국어 TTS", "대체 목소리를 선택하세요 (이 목소리에 없는 음절을 대신 읽어줄 조각 방식 목소리)."
                )
                return
            settings["fallback_bank"] = fallback

        voice_dir = os.path.join(SOUND_ROOT, name)
        os.makedirs(voice_dir, exist_ok=True)
        ktts.save_bank_manifest(voice_dir, {"schema_version": 1, "type": bank_type, "settings": settings, "audio": {}})
        self.new_bank_frame.pack_forget()
        self._open_bank(name)

    def _open_bank(self, name):
        self.voice_dir = os.path.join(SOUND_ROOT, name)
        manifest = ktts.load_bank_manifest(self.voice_dir)
        bank_type = manifest.get("type", ktts.BANK_TYPE_PIECES)
        entry = BANK_TYPE_BY_ID.get(bank_type)
        if entry is None:
            messagebox.showerror("한국어 TTS", f"이 도구에서 지원하지 않는 뱅크 종류입니다: {bank_type}")
            return

        settings = manifest.get("settings") or {}
        self.names = entry["names"](settings)
        self.prompts = entry["prompts"](settings)
        self.voice_hint.config(text=f"({entry['label']})")

        if bank_type == ktts.BANK_TYPE_PIECES:
            self.override_char_var.set("")
            self._update_override_status()
            self.override_frame.pack(fill="x", padx=10, pady=(0, 8))
        else:
            self.override_frame.pack_forget()

        self._refresh_voice_list()
        self._refresh_done_markers()
        self.index = self._first_unrecorded_index()
        self._show_current()
        self.record_btn.config(state="normal" if (mic_record and mic_record.is_available()) else "disabled")

    def _update_override_status(self):
        ch = self.override_char_var.get().strip()
        if not ch:
            self.override_status_label.config(text="")
            return
        if len(ch) != 1 or not ktts.is_syllable(ch):
            self.override_status_label.config(text="한 글자의 한글 음절을 입력하세요.")
            return
        if not hasattr(self, "voice_dir"):
            return
        name = ktts.syllable_filename(ch)
        exists = os.path.exists(os.path.join(self.voice_dir, name + ".wav"))
        self.override_status_label.config(text=f"파일: {name}.wav" + (" (이미 있음 - 덮어씀)" if exists else " (새로 만듦)"))

    def _accept_override(self):
        if not hasattr(self, "voice_dir"):
            return
        ch = self.override_char_var.get().strip()
        if len(ch) != 1 or not ktts.is_syllable(ch):
            messagebox.showwarning("한국어 TTS", "한 글자의 한글 음절을 입력하세요 (예: 학).")
            return
        if not self.current_take:
            messagebox.showinfo("한국어 TTS", "먼저 위의 ● 녹음 시작 / ■ 녹음 중지로 녹음하세요.")
            return
        name = ktts.syllable_filename(ch)
        path = os.path.join(self.voice_dir, name + ".wav")
        try:
            with open(path, "wb") as f:
                f.write(to_wav_bytes(self.current_take))
        except OSError as e:
            messagebox.showerror("한국어 TTS", f"저장 실패: {e}")
            return

        manifest = ktts.load_bank_manifest(self.voice_dir)
        settings = manifest.setdefault("settings", {})
        if not settings.get("syllable_overrides"):
            settings["syllable_overrides"] = True
            ktts.save_bank_manifest(self.voice_dir, manifest)

        self._update_override_status()
        self.status_label.config(text=f"'{ch}' 전체를 통째로 대체하는 녹음을 저장했습니다 ({name}.wav).")

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
            duration = len(pcm) / (mic_record.TARGET_RATE * mic_record.SAMPLE_WIDTH)
            peak = _peak_level(pcm)
            warn = ""
            if peak < 300:
                warn = ("  ⚠ 소리가 거의 감지되지 않았습니다 (최고 음량 "
                        f"{peak}/32767) - 마이크가 음소거되어 있거나, Windows 설정에서 "
                        "이 앱의 마이크 접근이 꺼져 있을 수 있습니다. 자세한 확인 방법은 "
                        "VOICE_RECORDER_README.txt의 '소리가 안 들릴 때' 참고.")
            self.status_label.config(
                text=f"녹음됨 ({duration:.2f}초, 최고 음량 {peak}/32767) - 들어보고 저장하세요.{warn}"
            )
            self.preview_btn.config(state="normal")
            self.accept_btn.config(state="normal")

    def _preview(self):
        if not self.current_take:
            return
        wav_bytes = to_wav_bytes(self.current_take)

        def work():
            try:
                ktts.play(wav_bytes)
            except Exception as e:
                # korean_tts.play()'s Windows path calls winsound.PlaySound
                # with no try/except of its own, so a real failure there
                # surfaces as a plain RuntimeError, not ktts.AudioError -
                # catch everything so a playback problem is never silently
                # invisible (this runs in a --windowed exe with no console).
                self.after(0, lambda: self.status_label.config(text=f"재생 실패: {e}"))

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
