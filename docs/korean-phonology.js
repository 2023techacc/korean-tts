// Korean text -> sequence of sound-sample names, plus the "how it's
// actually pronounced" display string.
//
// Direct port of korean_tts.py from the companion desktop project
// (tts.py/main.py) - same tables, same rule order, same known
// limitations. Keep the two in sync if either changes; see that file's
// comments for the full rationale (including why a couple of PyPI Korean
// G2P libraries were tried and rejected before hand-rolling this).
//
// Verified against Python: every one of the 11,172 possible Hangul
// syllables produces byte-identical sample sequences to korean_tts.py
// (see test-phonology.mjs), plus 34 multi-syllable phrases covering every
// rule family below.

const HANGUL_START = 0xac00;
const HANGUL_END = 0xd7a3;

const CHOSEONG = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ";
const JUNGSEONG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ";
const JONGSEONG = [""].concat(Array.from("ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"));

const COMPOUND_VOWELS = {
  ㅘ: "ㅗㅏ", ㅙ: "ㅗㅐ", ㅚ: "ㅗㅐ", ㅝ: "ㅜㅓ", ㅞ: "ㅜㅔ", ㅟ: "ㅜㅣ", ㅢ: "ㅡㅣ",
};
const VOWEL_MERGE = { ㅔ: "ㅐ", ㅖ: "ㅒ" };
const ROMAN = {
  ㅏ: "a", ㅓ: "eo", ㅐ: "ae", ㅡ: "eu", ㅣ: "i", ㅗ: "o", ㅜ: "u",
  ㅑ: "ya", ㅒ: "yae", ㅕ: "yeo", ㅛ: "yo", ㅠ: "yu",
  ㄱ: "g", ㄴ: "n", ㄷ: "d", ㄹ: "l", ㅁ: "m", ㅂ: "b", ㅅ: "s",
  ㅇ: "ng", ㅈ: "j", ㅊ: "ch", ㅋ: "k", ㅌ: "t", ㅍ: "p", ㅎ: "h",
  ㄲ: "gg", ㄸ: "dd", ㅆ: "ss", ㅉ: "jj", ㅃ: "bb",
};
// 음절의 끝소리 규칙: a batchim is neutralised to one of ㄱ ㄴ ㄷ ㄹ ㅁ ㅂ ㅇ.
const FINAL_MAP = {
  ㅅ: "ㄷ", ㅈ: "ㄷ", ㅊ: "ㄷ", ㅋ: "ㄱ", ㅌ: "ㄷ", ㅍ: "ㅂ", ㅎ: "ㄷ",
  ㄲ: "ㄱ", ㄸ: "ㄷ", ㅉ: "ㄷ", ㅃ: "ㅂ", ㅆ: "ㄷ", ㄳ: "ㄱ", ㄵ: "ㄴ",
  ㄶ: "ㄴ", ㅄ: "ㅂ", ㄼ: "ㄹ", ㄽ: "ㄹ", ㄾ: "ㄹ", ㅀ: "ㄹ", ㄺ: "ㄱ",
  ㄻ: "ㅁ", ㄿ: "ㅂ",
};
const CONSONANTS = new Set([...Object.keys(FINAL_MAP), "ㄱ", "ㄴ", "ㄷ", "ㄹ", "ㅁ", "ㅂ", "ㅇ"]);
const SONORANTS = new Set(["ㄴ", "ㄹ", "ㅁ", "ㅇ"]);

export const PAUSE = "stop";

// 겹받침(cluster finals) split into (kept, moved): "kept" stays as this
// syllable's batchim, "moved" becomes the next syllable's onset when
// liaison applies. Simple (non-cluster) finals fall back to ["", itself].
const JONG_CLUSTER = {
  ㄳ: ["ㄱ", "ㅅ"], ㄵ: ["ㄴ", "ㅈ"], ㄶ: ["ㄴ", "ㅎ"],
  ㄺ: ["ㄹ", "ㄱ"], ㄻ: ["ㄹ", "ㅁ"], ㄼ: ["ㄹ", "ㅂ"],
  ㄽ: ["ㄹ", "ㅅ"], ㄾ: ["ㄹ", "ㅌ"], ㅀ: ["ㄹ", "ㅎ"], ㄿ: ["ㄹ", "ㅍ"],
  ㅄ: ["ㅂ", "ㅅ"],
};

const NASALIZE_STOP = { ㄱ: "ㅇ", ㄷ: "ㄴ", ㅂ: "ㅁ" };
const ASPIRATE = { ㄱ: "ㅋ", ㄷ: "ㅌ", ㅂ: "ㅍ", ㅈ: "ㅊ" };
const H_CLUSTER_LEFTOVER = { ㄶ: "ㄴ", ㅀ: "ㄹ" };
const TENSIFY = { ㄱ: "ㄲ", ㄷ: "ㄸ", ㅂ: "ㅃ", ㅅ: "ㅆ", ㅈ: "ㅉ" };

function isSyllable(ch) {
  const code = ch.codePointAt(0);
  return code >= HANGUL_START && code <= HANGUL_END;
}

// A parsed slot is either a mutable {cho, jung, jong} object (a Hangul
// syllable) or a plain string (anything else - space, punctuation, Latin).
function parse(text) {
  const slots = [];
  for (const ch of text) {
    if (isSyllable(ch)) {
      const offset = ch.codePointAt(0) - HANGUL_START;
      slots.push({
        cho: CHOSEONG[Math.floor(offset / 588)],
        jung: JUNGSEONG[Math.floor((offset % 588) / 28)],
        jong: JONGSEONG[offset % 28],
      });
    } else {
      slots.push(ch);
    }
  }
  return slots;
}

// Cross-syllable assimilation: 격음화, 비음화, ㄹ-adjacent nasalisation/
// liquid assimilation, 연음화/ㅎ탈락, 경음화. Mutates the slot objects in
// place. Line-for-line port of korean_tts.py's _apply_context_rules -
// see that file for the full rationale and worked textbook examples.
function applyContextRules(slots) {
  for (let i = 0; i < slots.length - 1; i++) {
    const cur = slots[i];
    const nxt = slots[i + 1];
    if (typeof cur === "string" || typeof nxt === "string") continue; // word/phrase boundary

    const jong = cur.jong;
    if (!jong) continue;
    const onset = nxt.cho;

    if (onset === "ㅇ") {
      // 연음화 / ㅎ탈락. ㅇ itself never liaises (no syllable-initial "ng"
      // in Korean), so 강아지 stays 강아지.
      if (jong === "ㅇ") continue;
      if (jong === "ㅎ") {
        cur.jong = "";
        continue;
      }
      const [kept, moved] = JONG_CLUSTER[jong] || ["", jong];
      cur.jong = kept;
      if (moved !== "ㅎ") nxt.cho = moved; // the ㅎ half of ㄶ/ㅀ also just drops
      continue;
    }

    // Before a CONSONANT: figure out what the batchim actually sounds like
    // there (cluster-ending-in-ㅎ, plain ㅎ, or its representative
    // stop/nasal/liquid per 음절의 끝소리 규칙).
    let leftover, hasH;
    if (jong in H_CLUSTER_LEFTOVER) {
      leftover = H_CLUSTER_LEFTOVER[jong];
      hasH = true;
    } else if (jong === "ㅎ") {
      leftover = "";
      hasH = true;
    } else {
      leftover = FINAL_MAP[jong] ?? jong;
      hasH = false;
    }

    if (hasH && onset in ASPIRATE) {
      // 격음화: 좋다 -> 조타, 많고 -> 만코 (ㄶ/ㅀ's ㄴ/ㄹ leftover stays)
      cur.jong = leftover;
      nxt.cho = ASPIRATE[onset];
    } else if (hasH && onset === "ㄴ") {
      // 놓는 -> 논는, 않니 -> 안니; a ㅀ-leftover ㄹ instead triggers the
      // liquid rule below (옳니 -> 올리).
      cur.jong = leftover === "ㄴ" || leftover === "ㄹ" ? leftover : "ㄴ";
      if (cur.jong === "ㄹ") nxt.cho = "ㄹ";
    } else if (!hasH && onset === "ㅎ" && leftover in NASALIZE_STOP) {
      cur.jong = ""; // 각하 -> 가카 (coda merges into ㅎ as an aspirate)
      nxt.cho = ASPIRATE[leftover];
    } else if (!hasH && onset === "ㄹ") {
      if (leftover in NASALIZE_STOP) {
        cur.jong = NASALIZE_STOP[leftover]; // 협력 -> 혐녁
        nxt.cho = "ㄴ";
      } else if (leftover === "ㅁ" || leftover === "ㅇ") {
        nxt.cho = "ㄴ"; // 담력 -> 담녁, 종로 -> 종노
      } else if (leftover === "ㄴ") {
        // 신라 -> 실라 (the regular case; Sino-Korean exceptions like
        // 의견란 -> 의견난 aren't handled)
        cur.jong = "ㄹ";
        nxt.cho = "ㄹ";
      }
    } else if (!hasH && (onset === "ㄴ" || onset === "ㅁ")) {
      if (leftover in NASALIZE_STOP) {
        cur.jong = NASALIZE_STOP[leftover]; // 국물 -> 궁물, 밥물 -> 밤물
      } else if (leftover === "ㄹ" && onset === "ㄴ") {
        nxt.cho = "ㄹ"; // 칼날 -> 칼랄, 설날 -> 설랄
      }
    } else if (!hasH && onset in TENSIFY && (leftover === "ㄱ" || leftover === "ㄷ" || leftover === "ㅂ")) {
      // 경음화: 국밥 -> 국빱, 학교 -> 학꾜, 학생 -> 학쌩. Coda itself
      // doesn't change, only the following plain obstruent tenses.
      nxt.cho = TENSIFY[onset];
    }
  }
  return slots;
}

const CHO_INDEX = Object.fromEntries(Array.from(CHOSEONG).map((c, i) => [c, i]));
const JUNG_INDEX = Object.fromEntries(Array.from(JUNGSEONG).map((v, i) => [v, i]));
const JONG_INDEX = Object.fromEntries(JONGSEONG.map((j, i) => [j, i]));

function compose(cho, jung, jong) {
  return String.fromCodePoint(HANGUL_START + (CHO_INDEX[cho] * 21 + JUNG_INDEX[jung]) * 28 + JONG_INDEX[jong]);
}

// Rewrite text into its standard spoken form (all rules above applied).
// e.g. "옷이 좋아요" -> "오시 조아요", "읽었다" -> "일걷따". This is what
// actually gets voiced.
export function textToPronunciation(text) {
  const slots = applyContextRules(parse(text));
  let out = "";
  for (const s of slots) {
    if (typeof s === "string") {
      out += s;
      continue;
    }
    let jong = s.jong;
    // Any batchim that survived to here is staying put, so display it
    // exactly as it will be voiced: reduced to its representative sound.
    if (CONSONANTS.has(jong)) jong = FINAL_MAP[jong] ?? jong;
    out += compose(s.cho, s.jung, jong);
  }
  return out;
}

// {cho, jung, jong} -> flat jamo array: batchim neutralisation + compound-vowel splitting.
function syllableToJamo(cho, jung, jong) {
  const a = [cho, jung];
  if (jong) a.push(jong);
  if (CONSONANTS.has(a[a.length - 1])) {
    a[a.length - 1] = FINAL_MAP[a[a.length - 1]] ?? a[a.length - 1];
  }

  const b = [];
  for (const j of a) for (const ch of COMPOUND_VOWELS[j] ?? j) b.push(ch);
  const c = [];
  for (const j of b) for (const ch of VOWEL_MERGE[j] ?? j) c.push(ch);
  return c;
}

// Groups samples so the audio engine knows which adjacent ones are lobes
// of the same syllable (a compound-vowel glide, or a vowel + bare
// sonorant-coda recording) and should be crossfaded, vs a plain
// one-recording syllable that needs no help. See korean_tts.py's
// text_to_groups for the branch-by-branch derivation this mirrors exactly.
// Returns an array of {kind, names} - PAUSE is its own {kind: "single",
// names: [PAUSE]} group.
export function textToGroups(text) {
  const groups = [];
  function pause() {
    const last = groups[groups.length - 1];
    if (!last || last.names.length !== 1 || last.names[0] !== PAUSE) {
      groups.push({ kind: "single", names: [PAUSE] });
    }
  }

  for (const slot of applyContextRules(parse(text))) {
    if (typeof slot === "string") {
      pause();
      continue;
    }
    const c = syllableToJamo(slot.cho, slot.jung, slot.jong);
    if (c.some((j) => !(j in ROMAN))) {
      pause();
      continue;
    }

    const last = c[c.length - 1];
    if (c[0] === "ㅇ" && !SONORANTS.has(last) && c.length === 2 + (CONSONANTS.has(last) ? 1 : 0)) {
      // ㅇ-onset, simple vowel, obstruent coda or none: one dedicated
      // recording already covers this (압 -> "ab").
      groups.push({ kind: "single", names: [c.slice(1).map((j) => ROMAN[j]).join("")] });
    } else if (c[0] === "ㅇ" && SONORANTS.has(last)) {
      // ㅇ-onset + sonorant coda: length 3 = simple vowel (안 -> a+n),
      // length 4 = compound vowel too (왕 -> o+a+ng).
      const names = c.slice(1).map((j) => ROMAN[j]);
      groups.push({ kind: c.length === 4 ? "diphthong" : "coda", names });
    } else if (c[0] === "ㅇ") {
      // ㅇ-onset, compound vowel (simple-vowel case already handled above).
      const names = [ROMAN[c[1]], c.slice(2).map((j) => ROMAN[j]).join("")];
      groups.push({ kind: "diphthong", names });
    } else if (c.length === 2 && CONSONANTS.has(c[0])) {
      groups.push({ kind: "single", names: [ROMAN[c[0]] + ROMAN[c[1]]] });
    } else if (c.length === 2) {
      groups.push({ kind: "single", names: [ROMAN[c[0]], ROMAN[c[1]]] }); // unreachable; c[0] always in CONSONANTS
    } else if (CONSONANTS.has(last) && !SONORANTS.has(last)) {
      // Real onset + obstruent coda: length 3 = simple vowel, already one
      // recording per half (학 -> ha+ag). length 4 = compound vowel too
      // (확 -> ho+ag), still a glide split.
      const names = [ROMAN[c[0]] + ROMAN[c[1]], ROMAN[c[c.length - 2]] + ROMAN[last]];
      groups.push({ kind: c.length === 4 ? "diphthong" : "single", names });
    } else if (SONORANTS.has(last) && c.length === 4) {
      // Real onset + compound vowel + sonorant coda (웜 -> u+eo+m).
      groups.push({ kind: "diphthong", names: [ROMAN[c[0]] + ROMAN[c[1]], ROMAN[c[2]], ROMAN[c[3]]] });
    } else if (SONORANTS.has(last) && c.length === 3) {
      // Real onset + simple vowel + sonorant coda: the other half of the
      // "voiced batchim" case (반 -> ba+n).
      groups.push({ kind: "coda", names: [ROMAN[c[0]] + ROMAN[c[1]], ROMAN[c[2]]] });
    } else {
      // Real onset + compound vowel + no coda (과 -> go+a).
      groups.push({ kind: "diphthong", names: [ROMAN[c[0]] + ROMAN[c[1]], ROMAN[last]] });
    }
  }

  return groups;
}

// Flat sample-name sequence, same content as textToGroups but without the grouping.
export function textToSamples(text) {
  return textToGroups(text).flatMap((g) => g.names);
}
