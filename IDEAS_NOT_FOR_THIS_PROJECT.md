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
