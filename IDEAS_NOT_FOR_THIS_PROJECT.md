# Ideas considered, not built here

Things that came up while working on the Korean TTS project (`Allinone (2)/`)
that seemed like reasonable ideas in general, but not a good fit for *this*
project's constraints (pure Python standard library, no numpy/scipy/audioop,
simple concatenative splicing, meant to stay small and predictable). Parked
here instead of just forgotten, in case they're useful for something else
later.

---

## 1. Automatic vowel-boundary detection for CV+VC crossfade alignment

**Context**: `korean_tts.py`'s `dedicated_diphthongs` tier joins an onset+vowel
recording (CV, e.g. 가) to a vowel+coda recording (VC, e.g. 안) to build a
syllable like 간. The join currently overlaps a fixed duration/fraction of
each clip (see `CROSSFADE_FRACTION`/`_overlap_len`) rather than finding where
the vowel actually ends and the coda consonant actually begins in each
specific recording.

**The idea**: detect the real vowel→consonant boundary in the VC clip (and
the true onset→vowel boundary in the CV clip) so the crossfade window lands
exactly on stable vowel content in both clips, instead of a duration that's
just a reasonable guess.

**Why it's already less broken than it sounds**: a CV block has no coda by
definition, so its tail *is* vowel content. A VC block is recorded with a
null onset, so its head is vowel content until the coda starts. A fixed-
duration window is landing on the right region in the common case already —
it just can't adapt to how long that region actually is per recording.

**Why not to build it here**:
- This join only ever happens for *sonorant* codas (ㄴ/ㄹ/ㅁ/ㅇ) — stop codas
  take a different path. Nasals and liquids don't have a clean amplitude
  drop at the vowel→consonant transition the way a stop consonant does; it's
  a spectral shift, not a loudness one. A simple energy-envelope detector
  (cheap, matches the project's existing hand-rolled RMS/trim code) would
  likely misfire on exactly the codas this feature is for.
- A robust version needs at least a basic pitch/harmonic or formant-tracking
  step, which means real DSP (FFT or equivalent). This project is
  deliberately pure-stdlib with no numpy - implementing and validating a
  formant tracker by hand, well enough to trust it running unsupervised
  across an unknown voice actor's recordings, is a much bigger undertaking
  than anything else in this codebase.
- Failure mode is bad: a wrong boundary means an audible cut mid-vowel,
  which is worse than the current predictable (if imperfect) crossfade. Any
  such feature would need a human to review its choice per-syllable anyway,
  at which point the existing per-sample `crossfade_ms` override already
  gives full manual control for the rare syllable that needs it.

**Where this WOULD make sense**: a standalone audio-processing tool/library
(numpy/scipy allowed) built specifically around phoneme-boundary detection —
e.g. using a lightweight forced-aligner approach (energy + zero-crossing
rate + a simple formant estimate via LPC) trained/tuned on a handful of
sample recordings, exposed as a CLI that takes two WAV files and a coda
type and returns a suggested crossfade window. Could be reused well beyond
Korean TTS - anything doing concatenative synthesis with dedicated
CV/VC-style recordings has the same problem.

---

## 2. Full engine parity for Android/web (not just hex_pieces)

**Context**: the Python engine (`korean_tts.py`) has grown a real cascade -
`dedicated_diphthongs`, `syllable_overrides`, `fallback_bank`, four
phonology-distinction toggles, per-kind crossfade tuning, hex-named pieces.
The Android (Kotlin) and web (JS) ports have deliberately stayed pieces-
only this whole project: `KNOWN_BANK_TYPES = {"pieces"}` on both platforms
silently hides any voice using the higher tiers from their voice pickers
rather than trying (and possibly failing) to play it. When hex_pieces
needed porting to keep `sound/default` working there, that was done
narrowly - just the base-piece naming translation, not the rest of the
cascade.

**The idea**: port the *entire* cascade to Kotlin and JS, so a voice built
with `dedicated_diphthongs`/`syllable_overrides`/phonology distinctions
sounds identical (or at least available) on every platform, not just
CLI/desktop.

**Why not to build it here (for now)**: this is less "wrong for the
project" and more "a real, multi-day undertaking each time the Python side
gains a feature." Every future cascade change would need re-implementing
correctly in two more languages, by hand, with no shared test harness
beyond the existing `docs/tests/*.mjs` reference-dump comparisons (nothing
equivalent exists for Kotlin/Android - there's no automated Kotlin-vs-
Python regression check today, and this sandbox couldn't even compile-check
the Kotlin hex_pieces change for lack of a JDK). Doing this properly would
also want an actual Android build + on-device test pass per change, which
isn't something achievable here. The project has consistently chosen "keep
the harder platforms simple and correct" over "chase full parity," and
nothing about hex_pieces changed that tradeoff - it only crossed the line
because NOT porting it would have actively broken `sound/default`
everywhere, not just left a feature gap.

**Where this WOULD make sense**: if Android/web usage becomes a priority in
its own right (not just "the CLI/desktop voice should also be selectable
there"), or if a CI pipeline with a real Android build step + emulator/
instrumented test existed to catch port drift automatically - without that
safety net, hand-porting a growing cascade into two more languages is a
standing invitation for silent divergence.

---

## 3. 구개음화, 사잇소리, and lexical 유음화 exceptions

**Context**: `_parse_and_apply_rules()`'s pronunciation rules (liaison,
assimilation, tensification, nasalization, etc.) all decide what to do from
LOCAL jamo context - the current syllable's cho/jung/jong plus its
immediate neighbor(s), read straight off the composed Hangul text. The
engine's own header comment (`korean_tts.py`, right above `NASALIZE_STOP`)
names three well-known standard-pronunciation rules that don't fit that
model at all, and says so explicitly rather than silently under-handling
them: **구개음화** (a root-final ㄷ/ㅌ palatalizes to ㅈ/ㅊ before a
ㅣ-based suffix, e.g. 굳이→구지, 같이→가치, 밭이→바치), **사잇소리** (an
inserted or tensed consonant at a compound-noun boundary, e.g.
나무+잎→나뭇잎[나문닙], 손+가락→손까락), and **lexical exceptions to 유음화**
(의견란→의견난, not the regular 신라→실라 pattern 유음화 would otherwise
predict for the same ㄴ+ㄹ adjacency).

**Why not to build them here**:
- 구개음화 only fires across a root+suffix boundary, not within a single
  morpheme that happens to contain the identical jamo sequence - 굳이
  (root 굳- + suffix -이) palatalizes to 구지, but 마디 (one indivisible
  word) does not, even though both are just ㄷ followed by 이. Nothing in
  the composed Hangul text tells the two cases apart; it requires knowing
  where a morpheme boundary actually is, which needs a morphological
  analyzer backed by a dictionary, not another local jamo rule.
- 사잇소리 and the 유음화 exceptions are worse: both are largely listed per
  word/compound rather than predictable from sound alone - 나무+잎→나뭇잎
  inserts a whole extra consonant, 손+등→손등 only tenses the following
  consonant, plenty of superficially similar compounds insert nothing at
  all, and 의견란 nasalizes precisely because it's a specific Sino-Korean
  root, not because anything about its jamo differs from 신라's. Getting
  these right needs a real dictionary of exceptions, not a rule to encode.
- All three would need actual Korean NLP tooling (a morphological
  analyzer/tokenizer with a real lexicon - e.g. what KoNLPy, Kiwi, or
  MeCab-ko already provide) sitting in front of `_parse_and_apply_rules()`
  - a fundamentally heavier, different kind of dependency than anything
  else in this project (pure stdlib, character-level rules only; see the
  same comment block's note on evaluating and rejecting the `korean_
  romanizer` PyPI package for related reasons - missing 비음화, actively
  wrong 구개음화). It would also only matter for correctly reading
  arbitrary free text; a bank built around specific target sentences can
  already reach for `syllable_overrides` to force the right pronunciation
  for the handful of words that actually need it.

**Where this WOULD make sense**: a general-purpose Korean text-to-
pronunciation front end built on an existing morphological analyzer
(KoNLPy/Kiwi/MeCab-ko or similar - already solved, well-tested problems in
that ecosystem), feeding its output INTO this project's existing
`text_to_pronunciation`/`_parse_and_apply_rules` pipeline (which would
still own turning the corrected jamo sequence into piece lookups) rather
than reimplementing morphology/lexicon lookups from scratch here.

---

## 4. Phase-vocoder time-stretching (the other pitch-preserving speed option)

**Context**: `korean_tts.change_speed()` offers two speed-change methods -
the original `"resample"` (fast, pitch shifts with speed) and a hand-rolled
`"wsola"` (waveform similarity overlap-add, keeps pitch roughly constant by
re-using verbatim, phase-aligned slices of the original waveform at a
different rate - see `_wsola_time_stretch`'s docstring). A phase vocoder is
the OTHER classic way to get pitch-preserving time-stretch, and works
completely differently: it takes the input's short-time Fourier transform
(STFT), stretches time by resampling the sequence of FFT frames themselves
while correcting each frame's phase so bins stay coherent frame-to-frame,
then inverse-transforms back to a time-domain signal at the new duration.

**Why not to build it here**: it needs a real FFT, at real speed, applied
to overlapping windows across the whole clip. This project already
weighed and rejected numpy/scipy as a dependency (see idea #1 above, and
`AudioSettings`/the rest of this codebase's pure-`array`-module DSP) - a
phase vocoder is the clearest case yet for why: a naive pure-Python DFT is
O(n²) per frame, and even a hand-rolled radix-2 FFT (the standard fix,
itself real effort to write and validate correctly) is meaningfully more
code and more failure surface than WSOLA's time-domain "reuse and cross-
fade" approach for the same result at this project's scale (short spoken
sentences, not music or long-form audio with wide stretch ratios). WSOLA
also degrades more gracefully for speech specifically - a phase vocoder is
generally the better choice for LARGE stretch ratios or independent pitch
shifting (changing pitch without changing speed, which WSOLA can't do at
all), neither of which this project's speed slider (0.5x-2.0x, tied
one-to-one with speed) needs.

**Where this WOULD make sense**: a project already depending on numpy/
scipy (or willing to), wanting either a wider/more extreme stretch range,
independent pitch-shifting as its own feature, or higher audio fidelity at
large stretch factors than a time-domain method like WSOLA can deliver -
none of which apply to this project's use case of modestly speeding up or
slowing down short TTS output.
