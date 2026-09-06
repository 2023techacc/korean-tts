"""Korean text -> concatenated .wav speech, using the samples in sound/.

Pure standard library: no jamo, no numpy, no audioop (removed in Python 3.13).
Used by both tts.py (CLI) and main.py (Discord bot).
"""

import array
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import wave

# ------------------------------------------------------
# Hangul decomposition
# ------------------------------------------------------

HANGUL_START = 0xAC00
HANGUL_END = 0xD7A3

# Compatibility jamo, in Unicode composition order.
CHOSEONG = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
JUNGSEONG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
JONGSEONG = [""] + list("ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ")


def is_syllable(ch: str) -> bool:
    return HANGUL_START <= ord(ch) <= HANGUL_END


def decompose(ch: str) -> str:
    """'값' -> 'ㄱㅏㅄ'. Equivalent to jamo.j2hcj(jamo.h2j(ch))."""
    offset = ord(ch) - HANGUL_START
    return (
        CHOSEONG[offset // 588]
        + JUNGSEONG[(offset % 588) // 28]
        + JONGSEONG[offset % 28]
    )


# ------------------------------------------------------
# Pronunciation tables
# ------------------------------------------------------

COMPOUND_VOWELS = {"ㅘ": "ㅗㅏ", "ㅙ": "ㅗㅐ", "ㅚ": "ㅗㅐ", "ㅝ": "ㅜㅓ", "ㅞ": "ㅜㅔ", "ㅟ": "ㅜㅣ", "ㅢ": "ㅡㅣ"}
VOWEL_MERGE = {"ㅔ": "ㅐ", "ㅖ": "ㅒ"}
ROMAN = {
    "ㅏ": "a", "ㅓ": "eo", "ㅐ": "ae", "ㅡ": "eu", "ㅣ": "i", "ㅗ": "o", "ㅜ": "u",
    "ㅑ": "ya", "ㅒ": "yae", "ㅕ": "yeo", "ㅛ": "yo", "ㅠ": "yu",
    "ㄱ": "g", "ㄴ": "n", "ㄷ": "d", "ㄹ": "l", "ㅁ": "m", "ㅂ": "b", "ㅅ": "s",
    "ㅇ": "ng", "ㅈ": "j", "ㅊ": "ch", "ㅋ": "k", "ㅌ": "t", "ㅍ": "p", "ㅎ": "h",
    "ㄲ": "gg", "ㄸ": "dd", "ㅆ": "ss", "ㅉ": "jj", "ㅃ": "bb",
}
# 음절의 끝소리 규칙: a 받침 is neutralised to one of ㄱ ㄴ ㄷ ㄹ ㅁ ㅂ ㅇ.
FINAL_MAP = {
    "ㅅ": "ㄷ", "ㅈ": "ㄷ", "ㅊ": "ㄷ", "ㅋ": "ㄱ", "ㅌ": "ㄷ", "ㅍ": "ㅂ", "ㅎ": "ㄷ",
    "ㄲ": "ㄱ", "ㄸ": "ㄷ", "ㅉ": "ㄷ", "ㅃ": "ㅂ", "ㅆ": "ㄷ", "ㄳ": "ㄱ", "ㄵ": "ㄴ",
    "ㄶ": "ㄴ", "ㅄ": "ㅂ", "ㄼ": "ㄹ", "ㄽ": "ㄹ", "ㄾ": "ㄹ", "ㅀ": "ㄹ", "ㄺ": "ㄱ",
    "ㄻ": "ㅁ", "ㄿ": "ㅂ",
}
CONSONANTS = set(FINAL_MAP) | {"ㄱ", "ㄴ", "ㄷ", "ㄹ", "ㅁ", "ㅂ", "ㅇ"}
SONORANTS = {"ㄴ", "ㄹ", "ㅁ", "ㅇ"}

PAUSE = "stop"

CHO_INDEX = {c: i for i, c in enumerate(CHOSEONG)}
JUNG_INDEX = {v: i for i, v in enumerate(JUNGSEONG)}
JONG_INDEX = {j: i for i, j in enumerate(JONGSEONG)}

# 겹받침(cluster finals) split into (kept, moved): "kept" stays as this
# syllable's batchim, "moved" becomes the next syllable's onset when liaison
# applies. Simple (non-cluster) finals use ("", <itself>) via .get() below.
JONG_CLUSTER = {
    "ㄳ": ("ㄱ", "ㅅ"), "ㄵ": ("ㄴ", "ㅈ"), "ㄶ": ("ㄴ", "ㅎ"),
    "ㄺ": ("ㄹ", "ㄱ"), "ㄻ": ("ㄹ", "ㅁ"), "ㄼ": ("ㄹ", "ㅂ"),
    "ㄽ": ("ㄹ", "ㅅ"), "ㄾ": ("ㄹ", "ㅌ"), "ㅀ": ("ㄹ", "ㅎ"), "ㄿ": ("ㄹ", "ㅍ"),
    "ㅄ": ("ㅂ", "ㅅ"),
}


# ------------------------------------------------------
# Cross-syllable assimilation (연음화, 비음화, 유음화, 격음화, ㅎ탈락)
# ------------------------------------------------------
#
# The original version (and jamo) decomposed one syllable at a time, so e.g.
# "옷이" came out as disconnected "od" + "i" instead of the correctly linked
# "o" + "si", and "국물" was read exactly as spelled instead of "궁물". This
# pass rewrites the (cho, jung, jong) triples in place, one adjacent pair at
# a time, before the romanisation step below runs, so both text_to_samples
# and text_to_pronunciation see the corrected forms.
#
# I evaluated the `korean_romanizer` PyPI package before writing this rather
# than reinventing it blindly: it's pure Python (no JVM/mecab, unlike g2pK),
# but hands-on testing showed it doesn't implement obstruent-to-nasal 비음화
# at all (국물 -> 국물, unchanged) and its 구개음화 is actively wrong (해돋이
# -> 해도디 instead of 해도지). Its 격음화 (aspiration) does work correctly,
# which is what the rules below reproduce. Given those gaps, hand-rolling
# rules I can verify against textbook examples beat depending on it.
#
# Implemented, in the order Korean speech actually applies it — each pair is
# routed by the FOLLOWING syllable's onset, so the rule families never
# compete for the same case:
#   - 격음화 (aspiration): 좋다 -> 조타, 많고 -> 만코, 각하 -> 가카
#   - 비음화 (obstruent -> nasal before ㄴ/ㅁ): 국물 -> 궁물, 밥물 -> 밤물
#   - ㄹ-adjacent nasalisation/liquid assimilation: 협력 -> 혐녁 (stop+ㄹ),
#     담력 -> 담녁 (nasal+ㄹ), 신라 -> 실라 / 칼날 -> 칼랄 (ㄴ+ㄹ/ㄹ+ㄴ -> ㄹㄹ)
#   - 연음화 (liaison) + ㅎ탈락 before a vowel: 옷이 -> 오시, 좋아요 -> 조아요
#
# Deliberately NOT covered (these need a dictionary or morphology, not just
# adjacent jamo, to avoid getting common words wrong):
#   - 구개음화: 굳이 -> 구지 needs to fire, but 마디 (a single morpheme, no
#     hidden boundary) must NOT become 마지 — indistinguishable from the
#     surface characters alone.
#   - 경음화 in compounds (사잇소리) and the lexical exceptions to 유음화
#     (의견란 -> 의견난, not 의결란) — both depend on which specific words
#     are involved, not just which jamo are adjacent.
# So e.g. "굳이" still won't palatalize to "구지". --check and the
# docstrings don't claim more than what's listed above.

NASALIZE_STOP = {"ㄱ": "ㅇ", "ㄷ": "ㄴ", "ㅂ": "ㅁ"}  # obstruent -> nasal
ASPIRATE = {"ㄱ": "ㅋ", "ㄷ": "ㅌ", "ㅂ": "ㅍ", "ㅈ": "ㅊ"}  # + ㅎ -> aspirated
H_CLUSTER_LEFTOVER = {"ㄶ": "ㄴ", "ㅀ": "ㄹ"}  # coda clusters ending in ㅎ
TENSIFY = {"ㄱ": "ㄲ", "ㄷ": "ㄸ", "ㅂ": "ㅃ", "ㅅ": "ㅆ", "ㅈ": "ㅉ"}  # 경음화

# ------------------------------------------------------
# Local (single-syllable) vowel rules
# ------------------------------------------------------
#
# Unlike the cross-syllable rules below, these depend only on a syllable's
# own (cho, jung) — no neighbour involved — so they're applied once, up
# front, rather than as part of the pairwise pass:
#
#   - 구개음화된 자음(ㅈ,ㅉ,ㅊ) 뒤 반모음 탈락 (표준발음법 제5항 다만 1):
#     these three are already palatal, so a following /j/ glide is
#     phonetically inert - 져/쪄/쳐/쟤 etc. are pronounced exactly like
#     저/쩌/처/재. Applies to all six yotized vowels ㅑㅒㅕㅖㅛㅠ.
#   - ㅢ의 단모음화 (표준발음법 제5항 다만 3): ㅢ is pronounced [ㅣ] whenever
#     the syllable has a real consonant onset (희망 -> 히망, 무늬 -> 무니).
#     With onset ㅇ (or no onset) it keeps the ㅡ+ㅣ glide (의사 stays 의사),
#     which COMPOUND_VOWELS below still handles.
J_GLIDE_TO_PLAIN = {"ㅑ": "ㅏ", "ㅒ": "ㅐ", "ㅕ": "ㅓ", "ㅖ": "ㅔ", "ㅛ": "ㅗ", "ㅠ": "ㅜ"}
PALATAL_ONSETS = {"ㅈ", "ㅉ", "ㅊ"}


def _apply_local_vowel_rules(slots):
    for s in slots:
        if not isinstance(s, list):
            continue
        cho, jung = s[0], s[1]
        if cho in PALATAL_ONSETS and jung in J_GLIDE_TO_PLAIN:
            s[1] = J_GLIDE_TO_PLAIN[jung]
        elif jung == "ㅢ" and cho != "ㅇ":
            s[1] = "ㅣ"
    return slots


def _parse(text: str):
    """Hangul syllables become mutable [cho, jung, jong] triples; everything
    else (spaces, punctuation, latin) is kept as-is and blocks assimilation."""
    slots = []
    for ch in text:
        if is_syllable(ch):
            offset = ord(ch) - HANGUL_START
            slots.append([
                CHOSEONG[offset // 588],
                JUNGSEONG[(offset % 588) // 28],
                JONGSEONG[offset % 28],
            ])
        else:
            slots.append(ch)
    return slots


def _apply_context_rules(slots):
    for i in range(len(slots) - 1):
        cur, nxt = slots[i], slots[i + 1]
        if not isinstance(cur, list) or not isinstance(nxt, list):
            continue  # word/phrase boundary: none of these rules cross it

        jong = cur[2]
        if not jong:
            continue
        onset = nxt[0]

        if onset == "ㅇ":
            # 연음화 / ㅎ탈락: a batchim followed by a vowel-initial syllable
            # moves onto it (or, for ㅎ, just disappears) instead of staying.
            # ㅇ itself never liaises: Korean has no syllable-initial "ng",
            # so 강아지 stays 강아지, not 가앙지.
            if jong == "ㅇ":
                continue
            if jong == "ㅎ":
                cur[2] = ""
                continue
            kept, moved = JONG_CLUSTER.get(jong, ("", jong))
            cur[2] = kept
            if moved != "ㅎ":  # the ㅎ half of ㄶ/ㅀ also just drops
                nxt[0] = moved
            continue

        # Everything past this point is before a CONSONANT, so figure out
        # what the batchim actually sounds like there: cluster-ending-in-ㅎ,
        # plain ㅎ, or its representative stop/nasal/liquid (음절의 끝소리
        # 규칙 — the same reduction FINAL_MAP applies later for rendering,
        # done here too since these rules need to know it up front).
        if jong in H_CLUSTER_LEFTOVER:
            leftover, has_h = H_CLUSTER_LEFTOVER[jong], True
        elif jong == "ㅎ":
            leftover, has_h = "", True
        else:
            leftover, has_h = FINAL_MAP.get(jong, jong), False

        if has_h and onset in ASPIRATE:
            # 격음화: 좋다 -> 조타, 많고 -> 만코 (leftover ㄴ/ㄹ of ㄶ/ㅀ stays)
            cur[2] = leftover
            nxt[0] = ASPIRATE[onset]
        elif has_h and onset == "ㄴ":
            # 놓는 -> 논는, 않니 -> 안니; a ㅀ-leftover ㄹ instead triggers
            # the liquid rule below (옳니 -> 올리).
            cur[2] = leftover if leftover in ("ㄴ", "ㄹ") else "ㄴ"
            if cur[2] == "ㄹ":
                nxt[0] = "ㄹ"
        elif not has_h and onset == "ㅎ" and leftover in NASALIZE_STOP:
            cur[2] = ""  # 각하 -> 가카 (coda merges into ㅎ as an aspirate)
            nxt[0] = ASPIRATE[leftover]
        elif not has_h and onset == "ㄹ":
            if leftover in NASALIZE_STOP:
                cur[2] = NASALIZE_STOP[leftover]  # 협력 -> 혐녁
                nxt[0] = "ㄴ"
            elif leftover in ("ㅁ", "ㅇ"):
                nxt[0] = "ㄴ"  # 담력 -> 담녁, 종로 -> 종노
            elif leftover == "ㄴ":
                # 신라 -> 실라 (the regular case; a fixed list of
                # Sino-Korean exceptions like 의견란 -> 의견난 isn't handled)
                cur[2] = "ㄹ"
                nxt[0] = "ㄹ"
        elif not has_h and onset in ("ㄴ", "ㅁ"):
            if leftover in NASALIZE_STOP:
                cur[2] = NASALIZE_STOP[leftover]  # 국물 -> 궁물, 밥물 -> 밤물
            elif leftover == "ㄹ" and onset == "ㄴ":
                nxt[0] = "ㄹ"  # 칼날 -> 칼랄, 설날 -> 설랄
        elif not has_h and onset in TENSIFY and leftover in ("ㄱ", "ㄷ", "ㅂ"):
            # 경음화: 국밥 -> 국빱, 학교 -> 학꾜, 학생 -> 학쌩. Coda itself
            # doesn't change, only the following plain obstruent tenses.
            # This is the regular, exceptionless phoneme-adjacency case;
            # compound-word 사잇소리 (e.g. 손+가락 -> 손까락, where the coda
            # isn't even a stop) needs lexical info and isn't handled.
            nxt[0] = TENSIFY[onset]
    return slots


def _compose(cho, jung, jong):
    return chr(HANGUL_START + (CHO_INDEX[cho] * 21 + JUNG_INDEX[jung]) * 28 + JONG_INDEX[jong])


def syllable_filename(ch: str, naming: str = "hex-codepoint") -> str:
    """Filesystem-safe base name (no extension) for one composed Hangul
    character - used by the full-syllable bank type and by dedicated-
    diphthong pieces (see "Sound banks" below). Hex codepoint (ASCII, e.g.
    "ac00") avoids cross-platform Unicode filename-normalization mismatches
    (macOS tends to store NFD, Windows/Linux NFC - the same character can
    end up as different bytes on disk) that a raw Hangul filename would
    risk, and matches the ASCII-only convention every existing romanized
    piece name (ga.wav, ab.wav) already uses.
    """
    if naming == "hangul":
        return ch
    return f"{ord(ch):04x}"


def all_full_syllables() -> set:
    """Every composed Hangul syllable - the full-syllable-bank analogue of
    all_reachable_samples()."""
    return {chr(c) for c in range(HANGUL_START, HANGUL_END + 1)}


def _parse_and_apply_rules(text: str):
    """Decompose `text` into (cho, jung, jong) slots and apply every
    pronunciation rule (local vowel rules, then cross-syllable ones) -
    the shared first step behind text_to_pronunciation and text_to_groups."""
    return _apply_context_rules(_apply_local_vowel_rules(_parse(text)))


def text_to_pronunciation(text: str) -> str:
    """Rewrite `text` into its standard spoken form (all rules above applied).

    e.g. '옷이 좋아요' -> '오시 조아요', '읽었다' -> '일걷다'. This is what
    actually gets voiced; tts.py's -p flag exists so you can see it before
    committing to audio.
    """
    slots = _parse_and_apply_rules(text)
    out = []
    for s in slots:
        if not isinstance(s, list):
            out.append(s)
            continue
        cho, jung, jong = s
        # Any batchim that's survived to here is staying put (not liaising,
        # not merging with ㅎ), so display it exactly as text_to_samples
        # will voice it: reduced to its representative sound if needed.
        if jong in CONSONANTS:
            jong = FINAL_MAP.get(jong, jong)
        out.append(_compose(cho, jung, jong))
    return "".join(out)


def _syllable_to_jamo(cho, jung, jong):
    """(cho, jung, jong) -> flat jamo list, applying batchim neutralisation
    (only relevant if liaison above didn't already resolve/move it) and
    compound-vowel splitting."""
    a = [cho, jung] + ([jong] if jong else [])
    if a[-1] in CONSONANTS:
        a[-1] = FINAL_MAP.get(a[-1], a[-1])

    b = []
    for j in a:
        b += list(COMPOUND_VOWELS.get(j, j))
    c = []
    for j in b:
        c += list(VOWEL_MERGE.get(j, j))
    return c


# ------------------------------------------------------
# Sample grouping: which adjacent samples belong to one syllable
# ------------------------------------------------------
#
# The sound/ library only has dedicated recordings for onset+vowel units and
# vowel+OBSTRUENT-coda units (e.g. "ab.wav" for a closed 압-type syllable).
# It has no equivalent for a compound-vowel glide (ㅘ/ㅝ/ㅢ/etc, split into
# its parts by COMPOUND_VOWELS above) or for a SONORANT batchim (ㄴㄹㅁㅇ),
# which instead falls back to a bare single-jamo recording ("n.wav" etc) —
# and that bare recording turns out to run about as long as a full syllable
# (150-230ms) rather than a quick consonant tail. Playing two such clips
# fully back-to-back roughly doubles (or triples, for a diphthong-plus-coda
# syllable like 왕) the syllable's real duration.
#
# Each group below carries a `kind` tag so build_audio can crossfade (in
# _crossfade_join) the samples inside it instead of concatenating them
# whole — 'diphthong' gets a bigger overlap than 'coda' since blending two
# vowel qualities into a glide is the acoustically correct behaviour, while
# a coda consonant should stay mostly intact. 'single' groups (a plain
# syllable, or an obstruent coda already fused with its vowel into one
# recording) are left exactly as before.

def text_to_groups(text: str, dedicated_diphthong_check=None, naming: str = "hex-codepoint"):
    """Like text_to_samples, but keeps each character's sample name(s)
    grouped as (kind, [names]) so audio building knows which adjacent
    samples are lobes of the same syllable. A PAUSE is its own ('single',
    [PAUSE]) group. text_to_samples is just this, flattened.

    `dedicated_diphthong_check(name) -> bool` is an optional bank-supplied
    lookup (see korean_tts.py's "Sound banks" section / synthesize()): for
    a syllable whose vowel is a compound one (ㅘ/ㅝ/ㅢ/etc, normally split
    into two crossfaded pieces below), if this returns True for the
    dedicated fused recording's filename, that one recording is used
    instead of the usual split - with no coverage requirement, since the
    check runs per-syllable and falls through to the normal split whenever
    it returns False. Every existing caller passes None here (the default),
    which skips this entirely and leaves behavior identical to before.
    """
    groups = []

    def pause():
        # Collapse runs of unspeakable characters into one pause.
        if not groups or groups[-1][1] != [PAUSE]:
            groups.append(("single", [PAUSE]))

    for slot in _parse_and_apply_rules(text):
        if not isinstance(slot, list):
            pause()
            continue

        if dedicated_diphthong_check is not None and slot[1] in COMPOUND_VOWELS:
            cho, jung, jong = slot
            norm_jong = FINAL_MAP.get(jong, jong) if jong in CONSONANTS else jong
            if norm_jong in SONORANTS:
                # Sonorant coda: the dedicated recording only covers
                # onset+vowel-glide (no coda) - the coda is still the usual
                # shared bare-tail recording, appended as its own piece.
                name = syllable_filename(_compose(cho, jung, ""), naming)
                if dedicated_diphthong_check(name):
                    groups.append(("coda", [name, ROMAN[norm_jong]]))
                    continue
            else:
                # No coda, or an obstruent coda that composes into one
                # complete, real syllable - one dedicated recording covers
                # the whole thing.
                name = syllable_filename(_compose(cho, jung, norm_jong), naming)
                if dedicated_diphthong_check(name):
                    groups.append(("single", [name]))
                    continue

        c = _syllable_to_jamo(*slot)
        if any(j not in ROMAN for j in c):
            pause()
            continue

        if c[0] == "ㅇ" and c[-1] not in SONORANTS and len(c) == 2 + int(c[-1] in CONSONANTS):
            # ㅇ-onset, simple vowel, obstruent coda or none: one dedicated
            # recording already covers this (e.g. 압 -> "ab").
            groups.append(("single", ["".join(ROMAN[j] for j in c[1:])]))
        elif c[0] == "ㅇ" and c[-1] in SONORANTS:
            # ㅇ-onset + sonorant coda: len 3 = simple vowel ("안" -> a+n),
            # len 4 = compound vowel too ("왕" -> o+a+ng).
            names = [ROMAN[j] for j in c[1:]]
            groups.append(("diphthong" if len(c) == 4 else "coda", names))
        elif c[0] == "ㅇ":
            # ㅇ-onset, compound vowel (always, since the simple-vowel case
            # was already handled above), obstruent coda or none.
            names = [ROMAN[c[1]], "".join(ROMAN[j] for j in c[2:])]
            groups.append(("diphthong", names))
        elif len(c) == 2 and c[0] in CONSONANTS:
            groups.append(("single", [ROMAN[c[0]] + ROMAN[c[1]]]))
        elif len(c) == 2:
            groups.append(("single", [ROMAN[c[0]], ROMAN[c[1]]]))  # unreachable; c[0] is always in CONSONANTS
        elif c[-1] in CONSONANTS and c[-1] not in SONORANTS:
            # Real onset + obstruent coda: len 3 = simple vowel, already one
            # recording per half ("학" -> ha+ag, not flagged as an issue).
            # len 4 = compound vowel too ("확" -> ho+ag), still a glide split.
            names = [ROMAN[c[0]] + ROMAN[c[1]], ROMAN[c[-2]] + ROMAN[c[-1]]]
            groups.append(("diphthong" if len(c) == 4 else "single", names))
        elif c[-1] in SONORANTS and len(c) == 4:
            # Real onset + compound vowel + sonorant coda ("웜" -> u+eo+m).
            groups.append(("diphthong", [ROMAN[c[0]] + ROMAN[c[1]], ROMAN[c[2]], ROMAN[c[3]]]))
        elif c[-1] in SONORANTS and len(c) == 3:
            # Real onset + simple vowel + sonorant coda: the other half of
            # the "voiced batchim" case ("반" -> ba+n).
            groups.append(("coda", [ROMAN[c[0]] + ROMAN[c[1]], ROMAN[c[2]]]))
        else:
            # Real onset + compound vowel + no coda ("과" -> go+a).
            groups.append(("diphthong", [ROMAN[c[0]] + ROMAN[c[1]], ROMAN[c[-1]]]))

    return groups


def text_to_samples(text: str):
    """Map Korean text to the flat sequence of sample names under sound/.

    Anything that is not a composed Hangul syllable (spaces, latin letters,
    punctuation, emoji) becomes a single PAUSE marker rather than raising.
    """
    return [name for _, names in text_to_groups(text) for name in names]


def all_reachable_samples():
    """Every sample name any Korean text could ever ask for. Used by --check."""
    names = set()
    for code in range(HANGUL_START, HANGUL_END + 1):
        for name in text_to_samples(chr(code)):
            if name != PAUSE:
                names.add(name)
    return names


# ------------------------------------------------------
# Audio assembly
# ------------------------------------------------------

TARGET_RATE = 44100
SAMPLE_WIDTH = 2  # 16-bit signed

# The 268 bundled samples span a 45x RMS range (186 to 8493) and one file
# already peaks at 32659/32768, so quiet syllables are close to inaudible
# next to loud ones. These constants tune the loudness-matching in
# normalize_loudness(). Target/max_gain/peak_limit picked by measuring, for
# each candidate target, how many of the 268 samples would land >10-30%
# short of it after gain+peak capping — 3500/8.0/32000 gives a +4dB louder
# baseline than the original 2200/6.0/30000 while only the 3% quietest
# outliers (e.g. beu.wav at raw RMS 186) fall notably short, and even those
# still land at 40%+ of target rather than being silent.
NORMALIZE_TARGET_RMS = 3500
NORMALIZE_MAX_GAIN = 8.0      # cap how hard a near-silent clip gets boosted (avoids amplifying hiss)
NORMALIZE_PEAK_LIMIT = 32000  # headroom under int16 full scale so boosted clips don't clip
SILENCE_THRESHOLD = 250       # |amplitude| below this counts as silence for trimming


class AudioError(Exception):
    pass


def _resample(samples: array.array, src_rate: int) -> array.array:
    """Nearest-neighbour resample to TARGET_RATE. All bundled samples are
    already 44100 Hz, so this only guards against odd files being dropped in."""
    if src_rate == TARGET_RATE or not samples:
        return samples
    n = max(1, len(samples) * TARGET_RATE // src_rate)
    last = len(samples) - 1
    return array.array("h", (samples[min(last, i * src_rate // TARGET_RATE)] for i in range(n)))


MIN_SPEED = 0.5
MAX_SPEED = 2.0


def change_speed(samples: array.array, speed: float) -> array.array:
    """Speed up or slow down playback by `speed`x.

    This is the same nearest-neighbour resample as _resample above, just
    read backwards: telling it the audio's "source rate" was actually
    TARGET_RATE*speed and asking it to resample back down to TARGET_RATE
    drops (speed>1) or repeats (speed<1) samples in exactly the proportion
    needed to compress or stretch playback time by that factor.

    Pitch shifts with speed (like a variable-speed tape), since doing
    proper time-stretching without moving the pitch needs a real DSP
    library (phase vocoder / WSOLA) - out of scope for a zero-dependency
    tool. The Android app instead uses AudioTrack's built-in PlaybackParams,
    which time-stretches without a pitch shift; this is the deliberate
    trade-off on the Python side for staying dependency-free.
    """
    speed = max(MIN_SPEED, min(MAX_SPEED, speed))
    return _resample(samples, max(1, round(TARGET_RATE * speed)))


def _rms(samples: array.array) -> float:
    if not samples:
        return 0.0
    return math.sqrt(sum(x * x for x in samples) / len(samples))


def trim_silence(samples: array.array, threshold: int = SILENCE_THRESHOLD) -> array.array:
    """Drop near-silent padding from both ends.

    Several bundled clips carry inconsistent blank space before/after the
    actual sound, so the audible gap between syllables varied even with a
    fixed gap_ms. Trimming first means gap_ms is the whole story.
    """
    n = len(samples)
    start = 0
    while start < n and abs(samples[start]) < threshold:
        start += 1
    end = n
    while end > start and abs(samples[end - 1]) < threshold:
        end -= 1
    return samples[start:end]


def normalize_loudness(
    samples: array.array,
    target_rms: float = NORMALIZE_TARGET_RMS,
    max_gain: float = NORMALIZE_MAX_GAIN,
    peak_limit: int = NORMALIZE_PEAK_LIMIT,
) -> array.array:
    """Scale `samples` so every clip has roughly the same perceived loudness.

    Plain peak normalization (scale so the loudest sample hits some ceiling)
    doesn't fix this: two clips can share a peak but differ wildly in RMS if
    one is a short transient and the other sustained. RMS-matching does, but
    naive RMS-matching would try to blast a near-silent clip's noise floor up
    ~45x to hit the target, so the gain is capped by max_gain, and separately
    capped by peak_limit so a boosted clip doesn't clip.
    """
    if not samples:
        return samples
    rms = _rms(samples)
    if rms <= 0:
        return samples

    gain = min(target_rms / rms, max_gain)
    peak = max(max(samples), -min(samples))
    if peak > 0 and peak * gain > peak_limit:
        gain = peak_limit / peak

    if abs(gain - 1.0) < 0.01:
        return samples
    return array.array("h", (max(-32768, min(32767, int(x * gain))) for x in samples))


# ------------------------------------------------------
# Per-sample fine-tuning overrides
# ------------------------------------------------------
#
# The automatic pipeline (trim + RMS-normalize) handles the common case, but
# some individual recordings are still ambiguous or off (e.g. a listener
# reporting one specific syllable sounds like a different one). Rather than
# hand-editing constants for one sample, an optional JSON file layers a
# small manual correction on top of the automatic result, per sample name:
#
#   {"pa": {"gain_db": 3.0, "trim_start_ms": 15}, "beu": {"gain_db": 6.0}}
#
# Three more keys affect join *timing* rather than the sample's own audio,
# so build_audio applies them directly instead of routing them through
# apply_override:
#   - crossfade_ms: overlap duration (ms) to use for a join this sample
#     takes part in, replacing the automatic CROSSFADE_FRACTION-based
#     calculation for that one join.
#   - coda_max_ms: replaces CODA_MAX_MS just for this sample when it's used
#     as a sonorant batchim tail (only meaningful for CODA_TAILS names).
#   - stop_gap_ms: replaces the caller's global stop_gap_ms just for the
#     silence inserted after THIS sample, when it ends a stop-coda syllable.
#
# Every key is optional and defaults to a no-op (gain_db: 0, trim: 0, and the
# three timing keys falling back to the automatic/global behavior), so an
# empty or missing file changes nothing. This is intentionally NOT loaded
# automatically by build_audio — callers that want it (tts.py, the desktop
# GUI) call load_overrides() themselves and pass the result in, keeping
# build_audio's behavior fully determined by its explicit arguments.

DEFAULT_OVERRIDES_FILENAME = "sound_overrides.json"


# ------------------------------------------------------
# Multi-voice support
# ------------------------------------------------------
#
# Every voice, including the original recordings, is a same-named subfolder
# of sound_root - sound/default/ga.wav, sound/narrator2/ga.wav, etc. A
# voice can be partial (missing files just get reported the same way a
# missing sample always has). Nothing else about build_audio changes: a
# voice is resolved to a directory via voice_dir() before it's ever passed
# in as sound_dir.

DEFAULT_VOICE = "default"


def list_voices(sound_root: str) -> list:
    """Voices available under `sound_root`: every immediate subdirectory
    that contains at least one .wav file (sound_root/default/, sound_root/
    narrator2/, ...) - DEFAULT_VOICE always first if present, since every
    interface's voice picker expects it there."""
    voices = []
    try:
        entries = sorted(os.listdir(sound_root))
    except OSError:
        entries = []
    for name in entries:
        path = os.path.join(sound_root, name)
        if not os.path.isdir(path):
            continue
        try:
            has_wav = any(f.lower().endswith(".wav") for f in os.listdir(path))
        except OSError:
            has_wav = False
        if has_wav:
            voices.append(name)
    if DEFAULT_VOICE in voices:
        voices.remove(DEFAULT_VOICE)
        voices.insert(0, DEFAULT_VOICE)
    elif not voices:
        voices = [DEFAULT_VOICE]  # nothing recorded yet - still offer it as a target
    return voices


def voice_dir(sound_root: str, voice: str) -> str:
    """The actual sample directory for `voice` under `sound_root` - always
    a subfolder, including for DEFAULT_VOICE (sound_root/default/)."""
    return os.path.join(sound_root, voice or DEFAULT_VOICE)


def overrides_path_for_voice(base_path: str, voice: str) -> str:
    """Where a voice's fine-tuning overrides live: `base_path` unchanged for
    DEFAULT_VOICE (so existing sound_overrides.json files keep working),
    otherwise the voice name inserted before the extension, e.g.
    "sound_overrides.json" -> "sound_overrides.narrator2.json"."""
    if not voice or voice == DEFAULT_VOICE:
        return base_path
    root, ext = os.path.splitext(base_path)
    return f"{root}.{voice}{ext}"


# ------------------------------------------------------
# Sound banks (bank.json) - which recording scheme a voice uses
# ------------------------------------------------------
#
# A voice folder can optionally hold a bank.json declaring which assembly
# strategy its recordings use ("type") and settings for that strategy:
#
#   {
#     "schema_version": 1,
#     "type": "pieces",             # or "full-syllable"
#     "display_name": "내레이터 2",  # optional, UI label
#     "settings": {...},            # type-specific, see build_audio_full_syllable/synthesize
#     "audio": {...}                # optional per-bank AudioSettings overrides, see below
#   }
#
# A missing, unreadable, or malformed bank.json is always treated as
# {"type": "pieces", "settings": {}, "audio": {}} - exactly what every voice
# already was before bank.json existed, so sound/default/ (and any other
# hand-made voice folder) needs zero changes to keep working.

DEFAULT_BANK_MANIFEST_FILENAME = "bank.json"
BANK_TYPE_PIECES = "pieces"
BANK_TYPE_FULL_SYLLABLE = "full-syllable"

_DEFAULT_BANK_MANIFEST = {"schema_version": 1, "type": BANK_TYPE_PIECES, "settings": {}, "audio": {}}


class UnsupportedBankTypeError(AudioError):
    def __init__(self, bank_type):
        super().__init__(f"알 수 없는 사운드 뱅크 종류: {bank_type}")
        self.bank_type = bank_type


def load_bank_manifest(bank_dir: str) -> dict:
    """Read bank_dir/bank.json. Always returns a dict with schema_version/
    type/settings/audio present - a missing, unreadable, or non-dict file
    resolves to the all-defaults manifest (same defensive philosophy as
    load_overrides: a typo must never stop playback)."""
    path = os.path.join(bank_dir, DEFAULT_BANK_MANIFEST_FILENAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict):
        data = {}
    manifest = dict(_DEFAULT_BANK_MANIFEST)
    manifest.update(data)
    manifest.setdefault("settings", {})
    manifest.setdefault("audio", {})
    return manifest


def save_bank_manifest(bank_dir: str, manifest: dict) -> None:
    path = os.path.join(bank_dir, DEFAULT_BANK_MANIFEST_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)


def list_bank_files(bank_dir: str) -> set:
    """Base names (no extension) of every .wav file directly in bank_dir -
    the "what's actually recorded" half of a coverage check, shared by
    tts.py's --check and gui.py's tuning tab instead of each re-scanning
    the folder inline."""
    try:
        return {f[:-4] for f in os.listdir(bank_dir) if f.lower().endswith(".wav")}
    except OSError:
        return set()


def list_banks(sound_root: str) -> list:
    """list_voices() plus each voice's parsed bank.json - the richer API
    bank-type-aware callers (synthesize(), voice_recorder.py) use. Additive:
    list_voices() itself is unchanged, so every existing caller keeps
    working exactly as before."""
    banks = []
    for name in list_voices(sound_root):
        bank_dir = voice_dir(sound_root, name)
        banks.append({"name": name, "dir": bank_dir, "manifest": load_bank_manifest(bank_dir)})
    return banks


def load_overrides(path: str) -> dict:
    """Load per-sample fine-tuning overrides from `path`.

    Always returns a dict, even if the file is missing or invalid - a typo
    or half-edited overrides file must never stop playback from working.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_overrides(path: str, overrides: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(overrides, f, ensure_ascii=False, indent=2, sort_keys=True)


def apply_override(samples: array.array, override: dict) -> array.array:
    """Apply one sample's fine-tuning on top of the automatic pipeline: extra
    trim beyond the automatic silence trim, then a gain adjustment in dB.
    A missing/empty override, or one with all-default values, is a no-op.
    """
    if not override or not samples:
        return samples

    start_ms = max(0.0, override.get("trim_start_ms", 0))
    end_ms = max(0.0, override.get("trim_end_ms", 0))
    if start_ms or end_ms:
        start = min(len(samples), int(TARGET_RATE * start_ms / 1000))
        end = max(start, len(samples) - int(TARGET_RATE * end_ms / 1000))
        samples = samples[start:end]

    gain_db = override.get("gain_db", 0)
    if gain_db:
        gain = 10 ** (gain_db / 20)
        samples = array.array("h", (max(-32768, min(32767, int(x * gain))) for x in samples))

    return samples


def read_sample(path: str, normalize: bool = True, override: dict = None, audio_settings=None) -> array.array:
    """Load a .wav as mono 16-bit @ TARGET_RATE, trimmed and loudness-matched,
    then optionally fine-tuned further per `override` (see apply_override).
    `audio_settings` (an AudioSettings, see below) supplies the trim
    threshold / loudness targets - defaults to today's global constants."""
    settings = audio_settings if audio_settings is not None else AudioSettings()
    with wave.open(path, "rb") as w:
        channels = w.getnchannels()
        width = w.getsampwidth()
        rate = w.getframerate()
        raw = w.readframes(w.getnframes())

    if width != SAMPLE_WIDTH:
        raise AudioError(f"{os.path.basename(path)}: expected 16-bit audio, got {width * 8}-bit")

    samples = array.array("h")
    samples.frombytes(raw)
    if sys.byteorder == "big":
        samples.byteswap()  # WAV data is little-endian

    # The bundled sound/ folder mixes mono and stereo files; downmix to mono.
    if channels > 1:
        usable = len(samples) - (len(samples) % channels)
        samples = array.array(
            "h", (sum(samples[i:i + channels]) // channels for i in range(0, usable, channels))
        )

    samples = _resample(samples, rate)
    samples = trim_silence(samples, threshold=settings.silence_threshold)
    if normalize:
        samples = normalize_loudness(
            samples,
            target_rms=settings.normalize_target_rms,
            max_gain=settings.normalize_max_gain,
            peak_limit=settings.normalize_peak_limit,
        )
    if override:
        samples = apply_override(samples, override)
    return samples


def _apply_fade(samples: array.array, fade_len: int):
    """Taper both ends so butted-together samples don't click."""
    fade_len = min(fade_len, len(samples) // 2)
    for i in range(fade_len):
        factor = i / fade_len
        samples[i] = int(samples[i] * factor)
        samples[-1 - i] = int(samples[-1 - i] * factor)


# How much of the shorter of two adjacent clips to overlap, as a fraction of
# its length: generous for a diphthong (blending vowel qualities together IS
# the correct glide sound), lighter for a sonorant coda (don't swallow the
# consonant's identity, just trim the dead air of playing it as a whole
# separate syllable). Clamped in samples so a very short or very long clip
# doesn't produce a degenerate (near-zero or near-total) overlap.
CROSSFADE_FRACTION = {"diphthong": 0.50, "coda": 0.25}
CROSSFADE_MIN_MS = 20
CROSSFADE_MAX_MS = 150

# The bare consonant recordings standing in for a sonorant batchim (ㄴㄹㅁㅇ)
# run 150-230ms on their own — they were recorded as a full mini-syllable
# ("느", "르", ...), not a quick closure. A real batchim ends abruptly right
# after the vowel with no release, so these are truncated to a short
# closure before crossfading kicks in. Names are unambiguous: onset+vowel
# samples are always 2+ romanised letters glued together (e.g. "ba", "go"),
# so a bare "n"/"l"/"m"/"ng" only ever occurs in coda position.
CODA_TAILS = {"n", "l", "m", "ng"}
CODA_MAX_MS = 120
CODA_TAIL_FADE_MS = 20

# Closed syllables ending in an unreleased obstruent stop (represented ㄱ/ㄷ/
# ㅂ after 음절의 끝소리 규칙 — this covers ㅋ/ㄲ, ㅅ/ㅆ/ㅈ/ㅊ/ㅌ/ㅎ, ㅍ too,
# all neutralised to one of these three) run straight into the next
# syllable with zero gap, same as every other syllable boundary. A real stop
# closure has some hold time before release, so that reads as rushed. Every
# such syllable's last sample name is a "vowel+consonant" romanisation
# ending in exactly g/d/b (e.g. "ag", "ug", "ad") — the sole exception is
# the sonorant-coda tail "ng", excluded via CODA_TAILS below — so this can
# be detected from the sample name alone, with no need to plumb the
# phonology's (cho, jung, jong) triples through to build_audio.
STOP_CODA_ENDINGS = ("g", "d", "b")
DEFAULT_STOP_GAP_MS = 40


class AudioSettings:
    """Resolved audio-assembly constants for one bank. Every field defaults
    to exactly the module constants above, so AudioSettings() (no args) -
    what every function below falls back to when a caller doesn't supply
    one - reproduces today's behavior precisely. A bank's bank.json "audio"
    block (see resolve_audio_settings) can override any subset of these.
    """

    _DEFAULTS = {
        "normalize_target_rms": NORMALIZE_TARGET_RMS,
        "normalize_max_gain": NORMALIZE_MAX_GAIN,
        "normalize_peak_limit": NORMALIZE_PEAK_LIMIT,
        "silence_threshold": SILENCE_THRESHOLD,
        "crossfade_fraction": CROSSFADE_FRACTION,
        "crossfade_min_ms": CROSSFADE_MIN_MS,
        "crossfade_max_ms": CROSSFADE_MAX_MS,
        "coda_max_ms": CODA_MAX_MS,
        "coda_tail_fade_ms": CODA_TAIL_FADE_MS,
        "default_stop_gap_ms": DEFAULT_STOP_GAP_MS,
    }

    def __init__(self, **overrides):
        for key, default in self._DEFAULTS.items():
            if key == "crossfade_fraction" and key in overrides and isinstance(overrides[key], dict):
                value = {**CROSSFADE_FRACTION, **overrides[key]}
            else:
                value = overrides.get(key, default)
            setattr(self, key, value)


def resolve_audio_settings(manifest: dict) -> AudioSettings:
    """Build an AudioSettings from a bank manifest's "audio" block. Unknown
    keys in that block are silently ignored (forward compatibility); any
    field it doesn't mention falls back to today's global constant."""
    audio = manifest.get("audio") if isinstance(manifest, dict) else None
    return AudioSettings(**(audio if isinstance(audio, dict) else {}))


def _ends_in_stop_coda(name: str) -> bool:
    return bool(name) and name not in CODA_TAILS and name[-1] in STOP_CODA_ENDINGS


def _shorten_coda(samples: array.array, max_ms: int = CODA_MAX_MS, fade_ms: int = CODA_TAIL_FADE_MS) -> array.array:
    """Cut a bare sonorant-coda recording down to a quick closure and fade
    the new tail so the cut isn't audible as a click."""
    max_len = int(TARGET_RATE * max_ms / 1000)
    if len(samples) <= max_len:
        return samples
    cut = array.array("h", samples[:max_len])
    fade_len = min(int(TARGET_RATE * fade_ms / 1000), len(cut) // 2)
    for i in range(fade_len):
        factor = i / fade_len
        cut[-1 - i] = int(cut[-1 - i] * factor)
    return cut


def _overlap_len(kind: str, len_a: int, len_b: int, override_ms=None, audio_settings=None) -> int:
    settings = audio_settings if audio_settings is not None else AudioSettings()
    shorter = min(len_a, len_b)
    if not len_a or not len_b:
        return 0
    if override_ms is not None:
        ov = int(TARGET_RATE * override_ms / 1000)
        return max(0, min(ov, shorter - 1))
    fraction = settings.crossfade_fraction.get(kind, 0.0)
    if fraction <= 0:
        return 0
    lo = int(TARGET_RATE * settings.crossfade_min_ms / 1000)
    hi = int(TARGET_RATE * settings.crossfade_max_ms / 1000)
    ov = int(shorter * fraction)
    ov = max(lo, ov)           # at least crossfade_min_ms, if the clip allows
    ov = min(ov, hi)           # but no more than crossfade_max_ms
    ov = min(ov, shorter - 1)  # and never the entire shorter clip
    return max(0, ov)


def _crossfade_join(chunks, kind: str, names=None, overrides=None, audio_settings=None) -> array.array:
    """Overlap-add adjacent chunks with an equal-power crossfade instead of
    concatenating them whole, so the combined clip is shorter than the sum
    of its parts and the join sounds like one continuous sound, not two
    stacked recordings. `kind` picks how aggressive the overlap is.

    If `names`/`overrides` are given and either sample touching a join sets
    a `crossfade_ms` override, that replaces the automatic fraction-based
    overlap for that one join — the later sample's override wins if both
    sides set one.
    """
    overrides = overrides or {}
    result = array.array("h", chunks[0])
    for i in range(1, len(chunks)):
        nxt = chunks[i]
        override_ms = None
        if names:
            override_ms = overrides.get(names[i], {}).get("crossfade_ms")
            if override_ms is None:
                override_ms = overrides.get(names[i - 1], {}).get("crossfade_ms")
        ov = _overlap_len(kind, len(chunks[i - 1]), len(nxt), override_ms, audio_settings=audio_settings)
        ov = min(ov, len(result), len(nxt))
        if ov <= 0:
            result.extend(nxt)
            continue
        base = len(result) - ov
        for k in range(ov):
            t = (k + 1) / (ov + 1)
            fade_out = math.cos(t * math.pi / 2)
            fade_in = math.sin(t * math.pi / 2)
            mixed = result[base + k] * fade_out + nxt[k] * fade_in
            result[base + k] = max(-32768, min(32767, int(mixed)))
        result.extend(nxt[ov:])
    return result


def build_audio(groups, sound_dir, gap_ms=300, fade_ms=5, normalize=True, crossfade=True, speed=1.0,
                 stop_gap_ms=DEFAULT_STOP_GAP_MS, overrides=None, audio_settings=None):
    """Concatenate grouped samples (from text_to_groups) into one mono track.

    Each group's samples are crossfaded together (see _crossfade_join) rather
    than placed end to end, then the small edge fade is applied to the
    group's outer edges only — not to the internal join, which the crossfade
    already smooths. Returns (samples, missing) where `missing` lists sample
    names with no .wav file.

    `stop_gap_ms` inserts extra silence after a syllable ending in an
    obstruent stop batchim (ㄱ/ㄷ/ㅂ, see STOP_CODA_ENDINGS) and before the
    next syllable — everywhere else, adjacent syllables still run together
    with no gap. Set to 0 to disable.

    `speed` (clamped to [MIN_SPEED, MAX_SPEED]) is applied once to the whole
    assembled track at the end via change_speed(), so pauses speed up/slow
    down along with the speech, matching how a playback-speed control on a
    video/audio player behaves.

    `overrides` is an optional {sample_name: {gain_db, trim_start_ms,
    trim_end_ms}} dict (see load_overrides/apply_override) for per-sample
    fine-tuning on top of the automatic pipeline. Not loaded automatically —
    pass the result of load_overrides(path) explicitly if you want it.

    For backwards compatibility, `groups` may also be a flat list of names
    (as text_to_samples returns): each name is then treated as its own
    'single' group, with no crossfading.
    """
    settings = audio_settings if audio_settings is not None else AudioSettings()
    track = array.array("h")
    gap = array.array("h", bytes(int(TARGET_RATE * gap_ms / 1000) * SAMPLE_WIDTH))
    stop_gap = array.array("h", bytes(int(TARGET_RATE * stop_gap_ms / 1000) * SAMPLE_WIDTH))
    fade_len = int(TARGET_RATE * fade_ms / 1000)
    missing = []
    raw_cache = {}
    prev_ends_in_stop = False
    prev_stop_gap_ms = stop_gap_ms
    overrides = overrides or {}

    if groups and isinstance(groups[0], str):
        groups = [("single", [name]) for name in groups]

    def load_raw(name):
        if name not in raw_cache:
            path = os.path.join(sound_dir, name + ".wav")
            raw_cache[name] = (
                read_sample(path, normalize=normalize, override=overrides.get(name), audio_settings=settings)
                if os.path.exists(path) else None
            )
        return raw_cache[name]

    for kind, names in groups:
        if names == [PAUSE]:
            track.extend(gap)
            prev_ends_in_stop = False
            continue

        if prev_ends_in_stop and prev_stop_gap_ms:
            track.extend(array.array("h", bytes(int(TARGET_RATE * prev_stop_gap_ms / 1000) * SAMPLE_WIDTH))
                         if prev_stop_gap_ms != stop_gap_ms else stop_gap)

        chunks = []
        for i, name in enumerate(names):
            raw = load_raw(name)
            if raw is None:
                missing.append(name)
                continue
            chunk = array.array("h", raw)  # copy: about to be mutated
            if i > 0 and name in CODA_TAILS:  # a batchim, not this syllable's onset
                coda_max_ms = overrides.get(name, {}).get("coda_max_ms", settings.coda_max_ms)
                chunk = _shorten_coda(chunk, max_ms=coda_max_ms, fade_ms=settings.coda_tail_fade_ms)
            chunks.append(chunk)
        if not chunks:
            continue

        combined = (
            _crossfade_join(chunks, kind, names, overrides, audio_settings=settings)
            if crossfade and len(chunks) > 1
            else chunks[0]
        )
        if len(chunks) > 1 and not crossfade:
            for extra in chunks[1:]:
                combined.extend(extra)

        if fade_len:
            _apply_fade(combined, fade_len)
        track.extend(combined)
        prev_ends_in_stop = _ends_in_stop_coda(names[-1])
        prev_stop_gap_ms = overrides.get(names[-1], {}).get("stop_gap_ms", stop_gap_ms)

    if speed != 1.0:
        track = change_speed(track, speed)
    return track, missing


def build_audio_full_syllable(
    text, sound_dir, fallback_dir=None, fallback_manifest=None,
    gap_ms=300, fade_ms=5, normalize=True, speed=1.0, stop_gap_ms=None,
    audio_settings=None, naming="hex-codepoint",
):
    """Assemble `text` from one whole-syllable recording per character - the
    "full-syllable" bank type's builder, an alternative to build_audio's
    piece-assembly scheme (no crossfade/coda-shortening needed since each
    recording is already a complete, continuous sound).

    Liaison/assimilation still has to run first (_parse_and_apply_rules,
    the same step text_to_pronunciation uses) so the CORRECTED syllable is
    looked up — e.g. 옷이 looks up 오 then 시, not 옷 then 이 — followed by
    the same batchim neutralisation text_to_pronunciation applies (밖 -> 박)
    so the filename matches what's actually pronounced.

    If a syllable isn't recorded in `sound_dir` and `fallback_dir` is given,
    that one character is synthesized via the fallback bank's own
    (piece-based) build_audio instead of being dropped. Returns
    (samples, missing, fallback_used): `missing` lists characters found
    nowhere (this bank or its fallback), `fallback_used` lists characters
    served via the fallback rather than a dedicated recording of this bank's
    own — a fully separate list from `missing`, not a subset of it.
    """
    settings = audio_settings if audio_settings is not None else AudioSettings()
    stop_gap_ms = settings.default_stop_gap_ms if stop_gap_ms is None else stop_gap_ms
    fallback_settings = resolve_audio_settings(fallback_manifest) if fallback_manifest else AudioSettings()

    track = array.array("h")
    gap = array.array("h", bytes(int(TARGET_RATE * gap_ms / 1000) * SAMPLE_WIDTH))
    stop_gap = array.array("h", bytes(int(TARGET_RATE * stop_gap_ms / 1000) * SAMPLE_WIDTH))
    fade_len = int(TARGET_RATE * fade_ms / 1000)
    missing = []
    fallback_used = []
    prev_ends_in_stop = False
    pending_pause = False

    for slot in _parse_and_apply_rules(text):
        if not isinstance(slot, list):
            if not pending_pause:
                track.extend(gap)
                prev_ends_in_stop = False
                pending_pause = True
            continue
        pending_pause = False

        cho, jung, jong = slot
        norm_jong = FINAL_MAP.get(jong, jong) if jong in CONSONANTS else jong
        ch = _compose(cho, jung, norm_jong)
        name = syllable_filename(ch, naming)
        path = os.path.join(sound_dir, name + ".wav")

        if os.path.exists(path):
            samples = read_sample(path, normalize=normalize, audio_settings=settings)
        elif fallback_dir is not None:
            sub_groups = text_to_groups(ch)
            sub_track, sub_missing = build_audio(
                sub_groups, fallback_dir, gap_ms=0, fade_ms=fade_ms,
                normalize=normalize, audio_settings=fallback_settings,
            )
            if sub_missing or not len(sub_track):
                missing.append(ch)
                samples = None
            else:
                samples = sub_track
                fallback_used.append(ch)
        else:
            missing.append(ch)
            samples = None

        if samples is not None and len(samples):
            if prev_ends_in_stop and stop_gap_ms:
                track.extend(stop_gap)
            chunk = array.array("h", samples)
            if fade_len:
                _apply_fade(chunk, fade_len)
            track.extend(chunk)
            prev_ends_in_stop = norm_jong in {"ㄱ", "ㄷ", "ㅂ"}
        else:
            prev_ends_in_stop = False

    if speed != 1.0:
        track = change_speed(track, speed)
    return track, missing, fallback_used


def synthesize(text, sound_root, voice, **kwargs):
    """Bank-type-aware entry point: resolves `voice`'s bank.json and routes
    to the matching assembly strategy. Prefer this over manually chaining
    text_to_groups()+build_audio() so new bank types keep working without
    every caller needing its own type dispatch. Returns (samples, missing),
    same shape as build_audio - if the bank is "full-syllable", its
    fallback_used list (see build_audio_full_syllable) isn't part of this
    return value; call build_audio_full_syllable directly if you need it.

    Raises UnsupportedBankTypeError for any type this function doesn't
    recognize - deliberately not silently treated as "pieces", since a
    bank of some future type likely doesn't have flat <name>.wav pieces to
    fall back to at all.
    """
    bank_dir = voice_dir(sound_root, voice)
    manifest = load_bank_manifest(bank_dir)
    bank_type = manifest.get("type", BANK_TYPE_PIECES)
    settings_block = manifest.get("settings") or {}
    audio_settings = resolve_audio_settings(manifest)
    kwargs.setdefault("stop_gap_ms", audio_settings.default_stop_gap_ms)

    if bank_type == BANK_TYPE_PIECES:
        naming = settings_block.get("naming", "hex-codepoint")
        check_fn = None
        if settings_block.get("dedicated_diphthongs"):
            def check_fn(name, _dir=bank_dir):
                return os.path.exists(os.path.join(_dir, name + ".wav"))
        groups = text_to_groups(text, dedicated_diphthong_check=check_fn, naming=naming)
        return build_audio(groups, bank_dir, audio_settings=audio_settings, **kwargs)

    if bank_type == BANK_TYPE_FULL_SYLLABLE:
        fallback_name = settings_block.get("fallback_bank")
        fallback_dir = voice_dir(sound_root, fallback_name) if fallback_name else None
        fallback_manifest = load_bank_manifest(fallback_dir) if fallback_dir else None
        naming = settings_block.get("naming", "hex-codepoint")
        kwargs.pop("crossfade", None)
        kwargs.pop("overrides", None)
        samples, missing, _fallback_used = build_audio_full_syllable(
            text, bank_dir, fallback_dir=fallback_dir, fallback_manifest=fallback_manifest,
            audio_settings=audio_settings, naming=naming, **kwargs,
        )
        return samples, missing

    raise UnsupportedBankTypeError(bank_type)


def to_wav_bytes(samples: array.array) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(TARGET_RATE)
        data = samples
        if sys.byteorder == "big":
            data = array.array("h", samples)
            data.byteswap()
        w.writeframes(data.tobytes())
    return buf.getvalue()


def duration(samples: array.array) -> float:
    return len(samples) / TARGET_RATE


# ------------------------------------------------------
# Playback
# ------------------------------------------------------

def play(wav_bytes: bytes):
    """Play a WAV blob, blocking until it finishes."""
    if sys.platform == "win32":
        import winsound

        winsound.PlaySound(wav_bytes, winsound.SND_MEMORY)
        return

    players = (
        ["afplay"] if sys.platform == "darwin" else ["aplay", "-q"],
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"],
        ["paplay"],
    )
    path = None
    try:
        fd, path = tempfile.mkstemp(suffix=".wav")
        with os.fdopen(fd, "wb") as f:
            f.write(wav_bytes)
        for cmd in players:
            try:
                subprocess.run(cmd + [path], check=True)
                return
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue
        raise AudioError(
            "No audio player found. Install ffmpeg (ffplay) or use -o to save a file instead."
        )
    finally:
        if path and os.path.exists(path):
            os.remove(path)
