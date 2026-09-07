"""Voice recording tool for creating a new TTS voice.

Walks through the item set a bank's checked settings call for (see
SETTING_OPTIONS/build_walkthrough - the base romanized pieces are always
included, plus hex-named CV-blocks/coda-tails and/or exact whole-syllable
recordings depending on what's checked), shows a real Korean character to
say for each one, records it via mic_record.MicRecorder, and saves accepted
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

import array
import os
import shutil
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


def _pieces_names(settings: dict) -> list:
    return sorted(ktts.all_reachable_samples(ktts.resolve_phonology_options(settings)))


def _pieces_prompts(settings: dict) -> dict:
    # build_prompt_map() is an unconditional full cartesian product (every
    # onset x every ROMAN-covered vowel), independent of `settings` - it's
    # already a strict superset of every PhonologyOptions combination's
    # reachable-name set (verified: zero gaps for every field on its own or
    # combined), so no phonology-aware branching is needed here at all.
    return build_prompt_map()


def _hex_filter(chars: set, naming: str) -> dict:
    """char -> filename for every char in `chars`, EXCLUDING any whose
    filename coincidentally matches a romanized piece name (see korean_tts.
    ROMANIZED_PIECE_NAMES) - the engine's synthesize() permanently treats
    such a name as the romanized piece, never as a dedicated recording of
    that character (see korean_tts._exists_check's collision fix), so
    asking someone to record it for that purpose here would silently never
    be used that way. In practice this excludes exactly one syllable
    (뮳, whose hex codepoint happens to equal the existing "bbae" piece)."""
    return {
        ch: ktts.syllable_filename(ch, naming) for ch in chars
        if ktts.syllable_filename(ch, naming) not in ktts.ROMANIZED_PIECE_NAMES
    }


def _cv_and_tail_names(settings: dict) -> list:
    """Tier-2 hex names (see korean_tts.py's text_to_groups cascade): every
    CV block (onset+nucleus) then every coda tail (nucleus+coda), each in
    ascending-codepoint order, so a speaker stays in a similar mouth
    position through a batch (composed-Hangul codepoints already encode
    (cho,jung,jong) in mixed-radix order)."""
    naming = settings.get("naming", "hex-codepoint")
    phonology = ktts.resolve_phonology_options(settings)
    cv_names = sorted(_hex_filter(ktts.all_diphone_cv_blocks(phonology), naming).values())
    tail_names = sorted(_hex_filter(ktts.all_diphone_coda_tails(phonology), naming).values())
    return cv_names + tail_names


def _cv_and_tail_prompts(settings: dict) -> dict:
    naming = settings.get("naming", "hex-codepoint")
    phonology = ktts.resolve_phonology_options(settings)
    by_char = _hex_filter(ktts.all_diphone_cv_blocks(phonology), naming)
    by_char.update(_hex_filter(ktts.all_diphone_coda_tails(phonology), naming))
    return {name: ch for ch, name in by_char.items()}


def _exact_syllable_names(settings: dict) -> list:
    """Tier-1 hex names: every REACHABLE composed Hangul syllable (korean_
    tts.all_reachable_full_syllables()) - not the raw 11,172, since
    syllables like 쟈 always resolve to 자's filename before a lookup ever
    happens (see korean_tts.py's _apply_local_vowel_rules) and recording
    them would be pure wasted effort."""
    naming = settings.get("naming", "hex-codepoint")
    by_char = _hex_filter(ktts.all_reachable_full_syllables(ktts.resolve_phonology_options(settings)), naming)
    return sorted(by_char.values())


def _exact_syllable_prompts(settings: dict) -> dict:
    naming = settings.get("naming", "hex-codepoint")
    by_char = _hex_filter(ktts.all_reachable_full_syllables(ktts.resolve_phonology_options(settings)), naming)
    return {name: ch for ch, name in by_char.items()}


def required_count(settings: dict) -> int:
    """How many recordings a bank needs under `settings`. The base pieces
    count is always the floor (matches the "조각 방식" preset); checking
    "온셋+중성 · 중성+받침 조각 별도 녹음" (tier 2, the "디폰" preset) or
    "특정 음절 통째로 녹음" (tier 1, the "완전한 음절" preset) each add
    their own item set on top, de-duplicated by filename - romanized piece
    names and hex names never coincide (see ROMANIZED_PIECE_NAMES/
    _hex_filter), so this is just an additive union in practice."""
    names = set(_pieces_names(settings))
    if settings.get("dedicated_diphthongs"):
        names |= set(_cv_and_tail_names(settings))
    if settings.get("syllable_overrides"):
        names |= set(_exact_syllable_names(settings))
    return len(names)


def build_walkthrough(settings: dict) -> tuple:
    """(names, prompts) for the recorder's walkthrough: the union of
    whichever item sets are enabled, base pieces always first, then tier-2
    CV-blocks+coda-tails, then tier-1 exact syllables, de-duped by name (a
    name already seen keeps its first prompt - only relevant for the rare,
    intentional shared-recording case in all_diphone_coda_tails, where
    both prompts name the same character anyway). Every other part of App
    (mic capture, progress/resume, record/preview/accept/save mechanics,
    the whole UI shell) only ever needs this (names, prompts) pair, however
    many item sets went into it."""
    names = []
    prompts = {}
    seen = set()

    def add_all(name_list, prompt_map):
        for name in name_list:
            if name not in seen:
                seen.add(name)
                names.append(name)
                prompts[name] = prompt_map.get(name, "?")

    add_all(_pieces_names(settings), _pieces_prompts(settings))
    if settings.get("dedicated_diphthongs"):
        add_all(_cv_and_tail_names(settings), _cv_and_tail_prompts(settings))
    if settings.get("syllable_overrides"):
        add_all(_exact_syllable_names(settings), _exact_syllable_prompts(settings))
    return names, prompts


# Every optional setting selectable when creating/reopening a bank, as one
# flat list - nothing here is type-restricted anymore, since "type" isn't
# an engine concept (see korean_tts.synthesize/_migrate_legacy_type): every
# bank goes through the same cascade, and how much a bank has recorded at
# each tier is what determines how close to full-syllable-quality it gets.
# "조각 방식"/"디폰"/"완전한 음절" become PRESET BUTTONS (see App._apply_
# preset) that just set a good starting combination of these checkboxes -
# not baselines you're locked into. "counts" says whether checking a
# setting changes required_count()'s result - true for all six now (even
# dedicated_diphthongs/syllable_overrides have a well-defined item count
# in this unified model). Deliberately just plain dicts so a future
# distinction is one entry away - see korean_tts.PhonologyOptions's own
# docstring for how to add one at the engine level first.
SETTING_OPTIONS = [
    {"key": "preserve_consonant_ui", "label": "자음+ㅢ 구분 (예: 씌)", "counts": True},
    {"key": "distinguish_palatal_glide", "label": "구개음화 뒤 반모음 구분 (자/쟈, 저/져 등)", "counts": True},
    {"key": "distinguish_ae_e", "label": "ㅐ/ㅔ 구분", "counts": True},
    {"key": "distinguish_oe_wae", "label": "ㅚ/ㅙ/ㅞ 구분", "counts": True},
    {"key": "dedicated_diphthongs", "label": "온셋+중성 · 중성+받침 조각 별도 녹음", "counts": True},
    {"key": "syllable_overrides", "label": "특정 음절 통째로 녹음", "counts": True},
]
# One-shot preset buttons (see App._apply_preset): each just sets these two
# tier checkboxes to a good starting combination, matching the old type's
# rough shape, then the user can freely adjust any box afterward. Phonology
# checkboxes are left untouched by a preset click.
PRESETS = [
    {"label": "조각 방식", "settings": {"dedicated_diphthongs": False, "syllable_overrides": False}},
    {"label": "디폰", "settings": {"dedicated_diphthongs": True, "syllable_overrides": False}},
    {"label": "완전한 음절", "settings": {"dedicated_diphthongs": False, "syllable_overrides": True}},
]


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


# ------------------------------------------------------
# Waveform trim editor (same widget as gui.py's TuningTab - duplicated
# rather than shared, matching this project's existing convention of each
# standalone tool staying self-contained, e.g. _compose above already
# duplicates korean_tts._compose rather than importing gui.py)
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


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("한국어 TTS - 목소리 녹음")
        self.geometry("1220x700")

        self.prompts = {}
        self.names = []
        self.index = 0
        self.voice = tk.StringVar(value="")
        # No "type" state at all - see SETTING_OPTIONS/PRESETS. Every
        # checkbox here is independent; a preset button just sets a good
        # starting combination of them (see _apply_preset).
        self.setting_vars = {opt["key"]: tk.BooleanVar(value=False) for opt in SETTING_OPTIONS}
        self.fallback_var = tk.StringVar(value="")
        self._pending_new_name = None
        self.override_char_var = tk.StringVar(value="")
        self.current_take = None  # bytes of the just-recorded, not-yet-saved take
        self.recorder = None
        self.recording = False

        # Destructive file-editing panel state (see _build_edit_panel) - edits
        # the CURRENTLY SELECTED item's .wav directly, not a synthesis-time
        # override. Sliders always represent "change to apply on top of
        # what's on disk right now", reset to 0 after every apply/revert/
        # selection change - they're never a memory of past edits.
        self.edit_gain_var = tk.DoubleVar(value=0.0)
        self.edit_trim_start_var = tk.DoubleVar(value=0.0)
        self.edit_trim_end_var = tk.DoubleVar(value=0.0)
        self._edit_raw_cache = None  # (name, samples) for the currently loaded waveform

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

        # New-bank settings panel: hidden (not packed) until _load_voice()
        # finds the typed name doesn't exist yet - shown once per new bank,
        # never again once it's created (every setting here shapes the
        # walkthrough's item set/reachability, so changing it later would
        # orphan already-recorded files' naming/counting assumptions).
        # "조각 방식"/"디폰"/"완전한 음절" are PRESET BUTTONS (see PRESETS),
        # not a separate exclusive choice - clicking one just sets a good
        # starting combination of the checkboxes below, which the user can
        # then freely adjust.
        self.new_bank_frame = ttk.LabelFrame(self, text="새 목소리 만들기")

        preset_row = ttk.Frame(self.new_bank_frame)
        preset_row.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Label(preset_row, text="프리셋:").pack(side="left")
        for preset in PRESETS:
            ttk.Button(preset_row, text=preset["label"],
                       command=lambda p=preset: self._apply_preset(p)).pack(side="left", padx=4)

        fallback_row = ttk.Frame(self.new_bank_frame)
        fallback_row.pack(fill="x", padx=8, pady=4)
        ttk.Label(fallback_row, text="빠진 음절 대체 목소리 (선택 사항):").pack(side="left")
        self.fallback_combo = ttk.Combobox(fallback_row, textvariable=self.fallback_var, state="readonly", width=20)
        self.fallback_combo.pack(side="left", padx=6)

        ttk.Separator(self.new_bank_frame, orient="horizontal").pack(fill="x", padx=8, pady=6)

        # Scrollable so every checkbox stays reachable/readable regardless
        # of window height or how many settings exist - a fixed-height
        # canvas that no longer clips content the way a plain grid/pack
        # layout did.
        scroll_area = ttk.Frame(self.new_bank_frame)
        scroll_area.pack(fill="both", expand=True, padx=8, pady=4)
        settings_canvas = tk.Canvas(scroll_area, height=170, highlightthickness=0)
        settings_scrollbar = ttk.Scrollbar(scroll_area, orient="vertical", command=settings_canvas.yview)
        settings_canvas.configure(yscrollcommand=settings_scrollbar.set)
        settings_canvas.pack(side="left", fill="both", expand=True)
        settings_scrollbar.pack(side="left", fill="y")
        settings_inner = ttk.Frame(settings_canvas)
        settings_canvas.create_window((0, 0), window=settings_inner, anchor="nw")
        settings_inner.bind(
            "<Configure>", lambda _e: settings_canvas.configure(scrollregion=settings_canvas.bbox("all"))
        )

        def _on_mousewheel(event, _canvas=settings_canvas):
            _canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        settings_canvas.bind("<Enter>", lambda _e: settings_canvas.bind_all("<MouseWheel>", _on_mousewheel))
        settings_canvas.bind("<Leave>", lambda _e: settings_canvas.unbind_all("<MouseWheel>"))

        self.setting_count_labels = {}
        for opt in SETTING_OPTIONS:
            row_frame = ttk.Frame(settings_inner)
            row_frame.pack(fill="x", pady=1, anchor="w")
            ttk.Checkbutton(row_frame, text=opt["label"], variable=self.setting_vars[opt["key"]],
                             command=self._refresh_settings_panel).pack(side="left")
            count_label = ttk.Label(row_frame, text="", foreground="#888")
            count_label.pack(side="left", padx=6)
            self.setting_count_labels[opt["key"]] = count_label

        self.total_count_label = ttk.Label(self.new_bank_frame, text="", font=("", 10, "bold"))
        self.total_count_label.pack(anchor="w", padx=8, pady=(4, 4))

        ttk.Button(self.new_bank_frame, text="만들기", command=self._create_new_bank).pack(pady=(4, 8))
        # Not packed here - _prepare_new_bank_ui() packs it, _load_voice()/
        # _create_new_bank() pack_forget() it once a bank is open.

        # Patch-a-syllable panel for an already-open bank (see korean_tts.
        # py's syllable_overrides setting): an open-ended "fix the syllable
        # that sounds wrong" workflow, not a fixed walkthrough list, so it's
        # deliberately not part of SETTING_OPTIONS's walkthrough - just an
        # additive panel shown for every open bank now (syllable_overrides
        # is universal). Reuses the SAME record/preview controls above
        # (they don't reference a name at all, only self.current_take) -
        # only the save destination differs.
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
        self.body = body
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

        # Packed side="right" (and BEFORE `main` below, which expands to fill
        # whatever's left) so this panel keeps a fixed-width column instead
        # of being squeezed out by main's expand=True.
        self._build_edit_panel(body)

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
        """A brand-new voice name: show the settings panel instead of
        creating the folder right away. fallback_bank is now optional for
        every combination (the base pieces are always part of the
        walkthrough too, so a bank never STRICTLY needs an external
        fallback - it's just a convenience for syllables you choose not
        to record yourself), so every existing bank is offered as a
        candidate, not just pieces-type ones - as long as it doesn't
        itself have a fallback_bank set (keeps the one-hop invariant)."""
        self._pending_new_name = name
        fallback_choices = [
            b["name"] for b in ktts.list_banks(SOUND_ROOT)
            if not (b["manifest"].get("settings") or {}).get("fallback_bank")
        ]
        self.fallback_combo["values"] = [""] + fallback_choices
        self.fallback_var.set("")

        for var in self.setting_vars.values():
            var.set(False)
        self._refresh_settings_panel()
        # before=self.body: without this, packing a widget after `body`
        # (already packed with expand=True at startup) stacks it BELOW
        # body instead of between `top` and `body`, so body's expand
        # claims nearly all the window and this panel is squeezed into
        # whatever sliver is left - verified this left the "만들기" button
        # entirely unmapped (0 pixels) at the default window size.
        self.new_bank_frame.pack(fill="x", padx=10, pady=(0, 8), before=self.body)

    def _current_settings_dict(self) -> dict:
        return {key: True for key, var in self.setting_vars.items() if var.get()}

    def _apply_preset(self, preset):
        for key, value in preset["settings"].items():
            self.setting_vars[key].set(value)
        self._refresh_settings_panel()

    def _refresh_settings_panel(self):
        """Recomputes every live count in the new-bank panel (see
        required_count()) - called on every setting checkbox toggle (and
        every preset click) so the numbers never lag behind what's
        actually checked."""
        current_settings = self._current_settings_dict()
        self.total_count_label.config(text=f"필요 녹음 수: {required_count(current_settings)}개")

        for opt in SETTING_OPTIONS:
            hypothetical = dict(current_settings)
            hypothetical[opt["key"]] = True
            count_label = self.setting_count_labels[opt["key"]]
            count_label.config(text=f"(체크 시 총 {required_count(hypothetical)}개)")

    def _create_new_bank(self):
        name = self._pending_new_name
        if not name:
            return
        settings = self._current_settings_dict()
        fallback = self.fallback_var.get().strip()
        if fallback:
            settings["fallback_bank"] = fallback

        voice_dir = os.path.join(SOUND_ROOT, name)
        os.makedirs(voice_dir, exist_ok=True)
        # "type" is legacy/display-only now (see korean_tts._migrate_legacy_
        # type) - a new bank's real behavior comes entirely from `settings`.
        ktts.save_bank_manifest(
            voice_dir, {"schema_version": 1, "type": ktts.BANK_TYPE_PIECES, "settings": settings, "audio": {}}
        )
        self.new_bank_frame.pack_forget()
        self._open_bank(name)

    def _open_bank(self, name):
        self.voice_dir = os.path.join(SOUND_ROOT, name)
        manifest = ktts.load_bank_manifest(self.voice_dir)
        # Reuses korean_tts's own migration read so an already-shipped
        # "diphone"/"full-syllable"-type bank reopens with exactly the
        # walkthrough its implied settings would produce - never drifts
        # from what synthesize() actually does with that same bank.
        settings = ktts._migrate_legacy_type(manifest)
        self.names, self.prompts = build_walkthrough(settings)

        labels = ["조각"]
        if settings.get("dedicated_diphthongs"):
            labels.append("디폰")
        if settings.get("syllable_overrides"):
            labels.append("완전한 음절")
        self.voice_hint.config(text="(" + " + ".join(labels) + ")")

        self.override_char_var.set("")
        self._update_override_status()
        self.override_frame.pack(fill="x", padx=10, pady=(0, 8), before=self.body)

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
        backup_path = path[:-4] + ".orig"  # see _accept_and_next's comment: stale backup, new take
        if os.path.exists(backup_path):
            try:
                os.remove(backup_path)
            except OSError:
                pass

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
        self._refresh_edit_panel()

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
    # File editing (destructive - edits the CURRENTLY SELECTED item's own
    # .wav directly, unlike gui.py's TuningTab which only ever writes a
    # synthesis-time override to sound_overrides.json and never touches the
    # recording itself). Every edit backs up the pre-edit file to
    # <name>.orig the first time (never overwritten again, so it always
    # holds the true original take) so "되돌리기" can always restore it.
    # ------------------------------------------------------

    def _build_edit_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="다듬기 (선택한 파일 자체를 바꿈)")
        frame.pack(side="right", fill="y", padx=(8, 0))
        self.edit_frame = frame

        self.edit_name_label = ttk.Label(frame, text="(선택 없음)", font=("", 11, "bold"))
        self.edit_name_label.pack(anchor="w", padx=8, pady=8)

        ttk.Label(frame, text="파형 (드래그해서 자르기 구간 조절)").pack(anchor="w", padx=8)
        self.edit_waveform = WaveformEditor(frame, on_drag=self._on_edit_waveform_drag)
        self.edit_waveform.pack(padx=8, pady=(0, 8))
        self.edit_waveform.redraw()

        self.edit_gain_text = tk.StringVar(value="음량 보정: +0.0dB")
        ttk.Label(frame, textvariable=self.edit_gain_text).pack(anchor="w", padx=8)
        ttk.Scale(frame, from_=-12, to=12, orient="horizontal", variable=self.edit_gain_var,
                  command=self._on_edit_gain_change, length=300).pack(padx=8, pady=4, fill="x")

        self.edit_trim_start_text = tk.StringVar(value="시작 자르기: 0ms")
        ttk.Label(frame, textvariable=self.edit_trim_start_text).pack(anchor="w", padx=8)
        ttk.Scale(frame, from_=0, to=200, orient="horizontal", variable=self.edit_trim_start_var,
                  command=self._on_edit_trim_change, length=300).pack(padx=8, pady=4, fill="x")

        self.edit_trim_end_text = tk.StringVar(value="끝 자르기: 0ms")
        ttk.Label(frame, textvariable=self.edit_trim_end_text).pack(anchor="w", padx=8)
        ttk.Scale(frame, from_=0, to=200, orient="horizontal", variable=self.edit_trim_end_var,
                  command=self._on_edit_trim_change, length=300).pack(padx=8, pady=4, fill="x")

        btns1 = ttk.Frame(frame)
        btns1.pack(fill="x", padx=8, pady=(8, 2))
        ttk.Button(btns1, text="▶ 적용 시 미리듣기", command=self._preview_edit).pack(side="left")
        ttk.Button(btns1, text="▶ 저장된 파일 그대로 듣기", command=self._preview_edit_original).pack(
            side="left", padx=8
        )

        btns2 = ttk.Frame(frame)
        btns2.pack(fill="x", padx=8, pady=(2, 8))
        ttk.Button(btns2, text="적용 (파일에 저장)", command=self._apply_edit).pack(side="left")
        ttk.Button(btns2, text="정규화 (자동 음량 맞춤)", command=self._normalize_now).pack(side="left", padx=8)
        ttk.Button(btns2, text="되돌리기 (원본 복원)", command=self._revert_edit).pack(side="left", padx=8)

        self.edit_status_label = ttk.Label(frame, text="", foreground="#888", wraplength=440, justify="left")
        self.edit_status_label.pack(anchor="w", padx=8, pady=(0, 4))

        note = ("여기서 하는 편집은 sound_overrides.json이 아니라 실제 .wav 파일 자체를 바꿉니다 - "
                "이 목소리를 쓰는 모든 곳에 바로 반영됩니다. 처음 적용할 때 원본을 <이름>.orig 로 "
                "자동 백업하므로(여러 번 적용해도 최초 원본만 보관) '되돌리기'로 언제든 복원할 수 "
                "있습니다. 이음매 타이밍(교차 길이 등)처럼 파일 하나만으로는 바꿀 수 없는 설정은 "
                "여기 없습니다 - 그건 데스크톱 앱(gui.py/KoreanTTS.exe)의 고급 설정 탭에서 "
                "sound_overrides.json 으로 계속 다룹니다.")
        ttk.Label(frame, text=note, wraplength=440, foreground="#888").pack(anchor="w", padx=8, pady=(0, 8))

    def _sync_edit_labels(self):
        self.edit_gain_text.set(f"음량 보정: {self.edit_gain_var.get():+.1f}dB")
        self.edit_trim_start_text.set(f"시작 자르기: {int(self.edit_trim_start_var.get())}ms")
        self.edit_trim_end_text.set(f"끝 자르기: {int(self.edit_trim_end_var.get())}ms")

    def _on_edit_gain_change(self, _v):
        self._sync_edit_labels()

    def _on_edit_trim_change(self, _v):
        self._sync_edit_labels()
        self.edit_waveform.set_trim(self.edit_trim_start_var.get(), self.edit_trim_end_var.get())

    def _on_edit_waveform_drag(self, start_ms, end_ms):
        self.edit_trim_start_var.set(round(start_ms))
        self.edit_trim_end_var.set(round(end_ms))
        self._sync_edit_labels()

    def _refresh_edit_panel(self):
        self.edit_gain_var.set(0.0)
        self.edit_trim_start_var.set(0.0)
        self.edit_trim_end_var.set(0.0)
        self._sync_edit_labels()

        if not hasattr(self, "voice_dir") or not self.names:
            self.edit_name_label.config(text="(선택 없음)")
            self._edit_raw_cache = None
            self.edit_waveform.peaks = []
            self.edit_waveform.redraw()
            self.edit_status_label.config(text="")
            return

        name = self._current_name()
        self.edit_name_label.config(text=name)
        path = os.path.join(self.voice_dir, name + ".wav")
        if not os.path.exists(path):
            self._edit_raw_cache = None
            self.edit_waveform.peaks = []
            self.edit_waveform.redraw()
            self.edit_status_label.config(text="아직 녹음되지 않았습니다.")
            return

        try:
            raw = ktts.read_sample(path, normalize=False, trim=False)
        except Exception as e:
            self._edit_raw_cache = None
            self.edit_waveform.peaks = []
            self.edit_waveform.redraw()
            self.edit_status_label.config(text=f"불러오기 실패: {e}")
            return

        self._edit_raw_cache = (name, raw)
        self.edit_waveform.load(raw)
        self.edit_waveform.set_trim(0.0, 0.0)
        backup_path = path[:-4] + ".orig"
        self.edit_status_label.config(text="원본 백업 있음 (되돌리기 가능)" if os.path.exists(backup_path) else "")

    def _current_edit_override(self) -> dict:
        override = {}
        gain = round(self.edit_gain_var.get(), 1)
        if gain:
            override["gain_db"] = gain
        start_ms = int(self.edit_trim_start_var.get())
        if start_ms:
            override["trim_start_ms"] = start_ms
        end_ms = int(self.edit_trim_end_var.get())
        if end_ms:
            override["trim_end_ms"] = end_ms
        return override

    def _preview_edit(self):
        if not self._edit_raw_cache:
            return
        _name, raw = self._edit_raw_cache
        override = self._current_edit_override()
        samples = ktts.apply_override(array.array("h", raw), override)
        wav_bytes = ktts.to_wav_bytes(samples)

        def work():
            try:
                ktts.play(wav_bytes)
            except Exception as e:
                self.after(0, lambda: self.edit_status_label.config(text=f"재생 실패: {e}"))

        threading.Thread(target=work, daemon=True).start()

    def _preview_edit_original(self):
        if not hasattr(self, "voice_dir") or not self.names:
            return
        name = self._current_name()
        path = os.path.join(self.voice_dir, name + ".wav")
        if not os.path.exists(path):
            return

        def work():
            try:
                with open(path, "rb") as f:
                    ktts.play(f.read())
            except Exception as e:
                self.after(0, lambda: self.edit_status_label.config(text=f"재생 실패: {e}"))

        threading.Thread(target=work, daemon=True).start()

    def _backup_and_overwrite(self, path, samples) -> bool:
        """Shared by _apply_edit/_normalize_now: backs up the pre-edit file
        to <name>.orig on first use (never overwritten again, so it always
        holds the true original take), then writes `samples` over the
        original. Returns False (after showing an error) on any failure."""
        backup_path = path[:-4] + ".orig"
        if not os.path.exists(backup_path):
            try:
                shutil.copy(path, backup_path)
            except OSError as e:
                messagebox.showerror("한국어 TTS", f"백업 실패: {e}")
                return False
        try:
            with open(path, "wb") as f:
                f.write(ktts.to_wav_bytes(samples))
        except OSError as e:
            messagebox.showerror("한국어 TTS", f"저장 실패: {e}")
            return False
        return True

    def _apply_edit(self):
        if not hasattr(self, "voice_dir") or not self.names:
            return
        name = self._current_name()
        path = os.path.join(self.voice_dir, name + ".wav")
        if not os.path.exists(path):
            messagebox.showinfo("한국어 TTS", "이 항목은 아직 녹음되지 않았습니다.")
            return
        override = self._current_edit_override()
        if not override:
            messagebox.showinfo("한국어 TTS", "적용할 변경 사항이 없습니다 (슬라이더를 움직여보세요).")
            return

        try:
            raw = ktts.read_sample(path, normalize=False, trim=False)
        except Exception as e:
            messagebox.showerror("한국어 TTS", f"읽기 실패: {e}")
            return
        edited = ktts.apply_override(array.array("h", raw), override)

        if not self._backup_and_overwrite(path, edited):
            return

        self.edit_status_label.config(text=f"'{name}.wav'에 적용해 저장했습니다 (원본은 {name}.orig 로 보관됨).")
        self._refresh_edit_panel()
        self._refresh_done_markers()

    def _normalize_now(self):
        """One-click loudness normalization, baked directly into the file -
        the same normalize_loudness() the engine already applies at
        playback time (module defaults - this tool has no per-bank
        AudioSettings context), applied once and saved, through the same
        backup-then-overwrite safety net as _apply_edit. Additive to the
        gain/trim controls above, not a replacement for them."""
        if not hasattr(self, "voice_dir") or not self.names:
            return
        name = self._current_name()
        path = os.path.join(self.voice_dir, name + ".wav")
        if not os.path.exists(path):
            messagebox.showinfo("한국어 TTS", "이 항목은 아직 녹음되지 않았습니다.")
            return

        try:
            raw = ktts.read_sample(path, normalize=False, trim=False)
        except Exception as e:
            messagebox.showerror("한국어 TTS", f"읽기 실패: {e}")
            return
        normalized = ktts.normalize_loudness(array.array("h", raw))

        if not self._backup_and_overwrite(path, normalized):
            return

        self.edit_status_label.config(
            text=f"'{name}.wav'의 음량을 자동으로 맞춰 저장했습니다 (원본은 {name}.orig 로 보관됨)."
        )
        self._refresh_edit_panel()

    def _revert_edit(self):
        if not hasattr(self, "voice_dir") or not self.names:
            return
        name = self._current_name()
        path = os.path.join(self.voice_dir, name + ".wav")
        backup_path = path[:-4] + ".orig"
        if not os.path.exists(backup_path):
            messagebox.showinfo("한국어 TTS", "복원할 원본 백업이 없습니다.")
            return
        try:
            shutil.copy(backup_path, path)
        except OSError as e:
            messagebox.showerror("한국어 TTS", f"복원 실패: {e}")
            return

        self.edit_status_label.config(text=f"'{name}.wav'을 원본으로 복원했습니다.")
        self._refresh_edit_panel()

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
        # A fresh recording replaces the take entirely - any .orig backup
        # from the file-editing panel (see "다듬기") belonged to the OLD
        # take and would otherwise let a later "되돌리기" wrongly restore it
        # instead of this new one.
        backup_path = path[:-4] + ".orig"
        if os.path.exists(backup_path):
            try:
                os.remove(backup_path)
            except OSError:
                pass
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
