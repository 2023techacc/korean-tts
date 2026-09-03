# Korean TTS [![Download](https://img.shields.io/github/v/release/2023techacc/korean-tts?include_prereleases&label=Download&color=blue)](https://github.com/2023techacc/korean-tts/releases/latest)


Turns Korean text into speech by looking up recorded syllable pieces (`sound/*.wav`, 268 files) and concatenating them — no TTS model, no cloud API, no external dependencies beyond the Python/Kotlin/JS standard runtime. A hand-written phonology engine rewrites the input first (liaison, nasalization, liquid assimilation, aspiration, tensification, final-consonant neutralization) so e.g. `국물이` is spoken as `궁무리`, not read literally.

The same engine is implemented four times — Python (canonical), Kotlin, and JavaScript — one per interface below, kept in sync by hand and checked against each other with exhaustive tests (all 11,172 possible Hangul syllables, bit-exact audio comparison).

## Interfaces

| | What it is | Where |
|---|---|---|
| CLI | `tts.py` — zero-dependency command-line tool, play or save to `.wav` | [`Allinone (2)/`](Allinone%20%282%29/TTS_README.txt) |
| Desktop app | `gui.py` — Tkinter GUI, packaged as `KoreanTTS.exe`; adds per-sample fine-tuning (gain/trim/crossfade/coda timing), a waveform trim editor, and batch text→WAV export | [`Allinone (2)/`](Allinone%20%282%29/BUILD_EXE.txt) |
| Android app | Native Kotlin app; reads selected text from any app via the system text-selection menu | [`KoreanTTS-Android/`](KoreanTTS-Android/README.txt) |
| Web | Static site, runs entirely client-side (Web Audio API, no server) | [`docs/`](docs/README.txt), published via GitHub Pages |

Each has its own README linked above with setup/usage details specific to that platform.

## Repo layout

```
Allinone (2)/     CLI (tts.py) + desktop app (gui.py) + the shared engine (korean_tts.py)
                  sound/ - the 268 voice samples (canonical copy)
KoreanTTS-Android/  Android Studio project (Kotlin port of the engine)
docs/             Web version (JS port of the engine) - served at the repo's GitHub Pages URL
.github/workflows/  CI: builds the .exe and .apk and drafts a GitHub Release on a tag push
sync-sound-assets.bat  Copies Allinone (2)/sound/ into the Android and web copies
```

This repo tracks the TTS work only — a combined Discord bot (Pokémon RPG / music / school info) shares this folder locally but is intentionally gitignored, not part of this project's history.

## Voices

Every voice, including the original recordings, is a same-named subfolder under `sound/` — the original voice is `sound/default/`, an additional one is `sound/narrator2/` (same 268 file names), selectable in every interface (`--voice`/`--list-voices` on the CLI, a dropdown in the desktop app/Android/web) — see [`Allinone (2)/TTS_README.txt`](Allinone%20%282%29/TTS_README.txt)'s "여러 목소리" section for the full picture, including per-voice fine-tuning files and an Android-specific naming gotcha (folder names can't start with `_` or `.`).

Recording one: **`VoiceRecorder.exe`** (`Allinone (2)/voice_recorder.py`) walks through the 253 sample names the phonology engine actually uses, shows the real Korean character to say for each (mic capture via raw `winmm.dll` through ctypes — no extra dependency), and saves accepted takes straight into `sound/<name>/` — no separate conversion step, it's immediately usable everywhere above. See [`Allinone (2)/VOICE_RECORDER_README.txt`](Allinone%20%282%29/VOICE_RECORDER_README.txt).

`sound/` is duplicated three times (CLI, Android assets, web) because each platform needs its own bundled copy. After changing/re-recording a sample, or adding a voice folder, run:

```
sync-sound-assets.bat
```

from the repo root. It checks the CLI copy for missing/renamed files, mirrors it into the Android and web copies, and (re)writes `docs/sound/voices.json` — the manifest the web version's voice dropdown reads, since a static site can't list its own directories. Android additionally needs a rebuild to pick the change up; the web version picks it up on the next page load; the `.exe` needs a rebuild (see below).

## Building

- **CLI**: nothing to build, just `py tts.py`.
- **.exe**: see [`Allinone (2)/BUILD_EXE.txt`](Allinone%20%282%29/BUILD_EXE.txt) for the PyInstaller command.
- **Android**: `cd KoreanTTS-Android && ./gradlew assembleDebug` (or open in Android Studio) — see [`KoreanTTS-Android/README.txt`](KoreanTTS-Android/README.txt).
- **All three, automatically**: push a tag matching `v*` (e.g. `git tag v1.0.0 && git push origin v1.0.0`), or run the *Release* workflow manually from the Actions tab. It builds `KoreanTTS.exe`, `VoiceRecorder.exe`, and the APK, and opens a draft GitHub Release with them attached — review and publish it yourself.

## If you change the engine

`korean_tts.py` is the source of truth. A change there (a phonology rule, an audio-pipeline constant) needs the same change made by hand in `KoreanTTS-Android/app/src/main/java/kr/koreantts/app/{KoreanPhonology,AudioEngine}.kt` and `docs/{korean-phonology,korean-audio}.js`, then re-verified — the test scripts under `docs/tests/` compare the JS port's output against a reference dump from the Python engine and are the fastest way to catch a divergence.
