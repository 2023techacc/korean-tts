package kr.koreantts.app

/**
 * Korean text -> sequence of sound-sample names, plus the "how it's actually
 * pronounced" display string. This is a direct port of korean_tts.py from
 * the companion desktop project (tts.py / main.py) — same tables, same rule
 * order, same known limitations. Keep the two in sync if either changes;
 * see that file's comments for the full rationale (including why a couple
 * of PyPI Korean G2P libraries were tried and rejected before hand-rolling
 * this).
 *
 * Pure Kotlin, no Android dependency, so it can be unit-tested on its own.
 */
object KoreanPhonology {

    private const val HANGUL_START = 0xAC00
    private const val HANGUL_END = 0xD7A3

    private const val CHOSEONG = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
    private const val JUNGSEONG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
    private val JONGSEONG: List<String> = listOf("") + "ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ".map { it.toString() }

    private val COMPOUND_VOWELS = mapOf(
        "ㅘ" to "ㅗㅏ", "ㅙ" to "ㅗㅐ", "ㅚ" to "ㅗㅐ", "ㅝ" to "ㅜㅓ",
        "ㅞ" to "ㅜㅔ", "ㅟ" to "ㅜㅣ", "ㅢ" to "ㅡㅣ",
    )
    private val VOWEL_MERGE = mapOf("ㅔ" to "ㅐ", "ㅖ" to "ㅒ")
    private val ROMAN = mapOf(
        "ㅏ" to "a", "ㅓ" to "eo", "ㅐ" to "ae", "ㅡ" to "eu", "ㅣ" to "i", "ㅗ" to "o", "ㅜ" to "u",
        "ㅑ" to "ya", "ㅒ" to "yae", "ㅕ" to "yeo", "ㅛ" to "yo", "ㅠ" to "yu",
        "ㄱ" to "g", "ㄴ" to "n", "ㄷ" to "d", "ㄹ" to "l", "ㅁ" to "m", "ㅂ" to "b", "ㅅ" to "s",
        "ㅇ" to "ng", "ㅈ" to "j", "ㅊ" to "ch", "ㅋ" to "k", "ㅌ" to "t", "ㅍ" to "p", "ㅎ" to "h",
        "ㄲ" to "gg", "ㄸ" to "dd", "ㅆ" to "ss", "ㅉ" to "jj", "ㅃ" to "bb",
    )
    // 음절의 끝소리 규칙: a batchim is neutralised to one of ㄱ ㄴ ㄷ ㄹ ㅁ ㅂ ㅇ.
    private val FINAL_MAP = mapOf(
        "ㅅ" to "ㄷ", "ㅈ" to "ㄷ", "ㅊ" to "ㄷ", "ㅋ" to "ㄱ", "ㅌ" to "ㄷ", "ㅍ" to "ㅂ", "ㅎ" to "ㄷ",
        "ㄲ" to "ㄱ", "ㄸ" to "ㄷ", "ㅉ" to "ㄷ", "ㅃ" to "ㅂ", "ㅆ" to "ㄷ", "ㄳ" to "ㄱ", "ㄵ" to "ㄴ",
        "ㄶ" to "ㄴ", "ㅄ" to "ㅂ", "ㄼ" to "ㄹ", "ㄽ" to "ㄹ", "ㄾ" to "ㄹ", "ㅀ" to "ㄹ", "ㄺ" to "ㄱ",
        "ㄻ" to "ㅁ", "ㄿ" to "ㅂ",
    )
    private val CONSONANTS: Set<String> = FINAL_MAP.keys + setOf("ㄱ", "ㄴ", "ㄷ", "ㄹ", "ㅁ", "ㅂ", "ㅇ")
    private val SONORANTS = setOf("ㄴ", "ㄹ", "ㅁ", "ㅇ")

    const val PAUSE = "stop"

    private val CHO_INDEX: Map<Char, Int> = CHOSEONG.withIndex().associate { (i, c) -> c to i }
    private val JUNG_INDEX: Map<Char, Int> = JUNGSEONG.withIndex().associate { (i, c) -> c to i }
    private val JONG_INDEX: Map<String, Int> = JONGSEONG.withIndex().associate { (i, j) -> j to i }

    // 겹받침(cluster finals) split into (kept, moved): "kept" stays as this
    // syllable's batchim, "moved" becomes the next syllable's onset when
    // liaison applies. Simple (non-cluster) finals fall back to ("", itself).
    private val JONG_CLUSTER = mapOf(
        "ㄳ" to ("ㄱ" to "ㅅ"), "ㄵ" to ("ㄴ" to "ㅈ"), "ㄶ" to ("ㄴ" to "ㅎ"),
        "ㄺ" to ("ㄹ" to "ㄱ"), "ㄻ" to ("ㄹ" to "ㅁ"), "ㄼ" to ("ㄹ" to "ㅂ"),
        "ㄽ" to ("ㄹ" to "ㅅ"), "ㄾ" to ("ㄹ" to "ㅌ"), "ㅀ" to ("ㄹ" to "ㅎ"), "ㄿ" to ("ㄹ" to "ㅍ"),
        "ㅄ" to ("ㅂ" to "ㅅ"),
    )

    private val NASALIZE_STOP = mapOf("ㄱ" to "ㅇ", "ㄷ" to "ㄴ", "ㅂ" to "ㅁ")
    private val ASPIRATE = mapOf("ㄱ" to "ㅋ", "ㄷ" to "ㅌ", "ㅂ" to "ㅍ", "ㅈ" to "ㅊ")
    private val H_CLUSTER_LEFTOVER = mapOf("ㄶ" to "ㄴ", "ㅀ" to "ㄹ")
    private val TENSIFY = mapOf("ㄱ" to "ㄲ", "ㄷ" to "ㄸ", "ㅂ" to "ㅃ", "ㅅ" to "ㅆ", "ㅈ" to "ㅉ")

    // Local (single-syllable) vowel rules - depend only on a syllable's own
    // (cho, jung), so applied once up front rather than as part of the
    // pairwise pass. See korean_tts.py's J_GLIDE_TO_PLAIN/PALATAL_ONSETS
    // comment for the full rationale (표준발음법 제5항 다만 1, 다만 3).
    private val J_GLIDE_TO_PLAIN = mapOf("ㅑ" to "ㅏ", "ㅒ" to "ㅐ", "ㅕ" to "ㅓ", "ㅖ" to "ㅔ", "ㅛ" to "ㅗ", "ㅠ" to "ㅜ")
    private val PALATAL_ONSETS = setOf("ㅈ", "ㅉ", "ㅊ")

    private fun isSyllable(ch: Char): Boolean = ch.code in HANGUL_START..HANGUL_END

    /** A decomposed Hangul syllable; mutated in place by [applyContextRules]. */
    private class Syl(var cho: String, var jung: String, var jong: String)

    /** [Syl] for a Hangul character, or the raw Char for anything else (space, punctuation, Latin). */
    private sealed class Slot {
        data class Hangul(val syl: Syl) : Slot()
        data class Other(val ch: Char) : Slot()
    }

    private fun parse(text: String): List<Slot> = text.map { ch ->
        if (isSyllable(ch)) {
            val offset = ch.code - HANGUL_START
            Slot.Hangul(Syl(CHOSEONG[offset / 588].toString(), JUNGSEONG[(offset % 588) / 28].toString(), JONGSEONG[offset % 28]))
        } else {
            Slot.Other(ch)
        }
    }

    /** Palatal-glide deletion after ㅈ/ㅉ/ㅊ, and ㅢ -> ㅣ after a real consonant onset. */
    private fun applyLocalVowelRules(slots: List<Slot>) {
        for (slot in slots) {
            val syl = (slot as? Slot.Hangul)?.syl ?: continue
            val glide = J_GLIDE_TO_PLAIN[syl.jung]
            if (syl.cho in PALATAL_ONSETS && glide != null) {
                syl.jung = glide
            } else if (syl.jung == "ㅢ" && syl.cho != "ㅇ") {
                syl.jung = "ㅣ"
            }
        }
    }

    /**
     * Cross-syllable assimilation: 격음화, 비음화, ㄹ-adjacent nasalisation/
     * liquid assimilation, 연음화/ㅎ탈락, 경음화. Mutates the Syl objects in
     * `slots` in place. See korean_tts.py's `_apply_context_rules` for the
     * full rationale and the worked textbook examples this was verified
     * against — this function is a line-for-line port of that logic.
     */
    private fun applyContextRules(slots: List<Slot>) {
        for (i in 0 until slots.size - 1) {
            val cur = slots[i] as? Slot.Hangul ?: continue
            val nxt = slots[i + 1] as? Slot.Hangul ?: continue
            val curSyl = cur.syl
            val nxtSyl = nxt.syl

            val jong = curSyl.jong
            if (jong.isEmpty()) continue
            val onset = nxtSyl.cho

            if (onset == "ㅇ") {
                // 연음화 / ㅎ탈락. ㅇ itself never liaises (no syllable-
                // initial "ng" in Korean), so 강아지 stays 강아지.
                if (jong == "ㅇ") continue
                if (jong == "ㅎ") {
                    curSyl.jong = ""
                    continue
                }
                val (kept, moved) = JONG_CLUSTER[jong] ?: ("" to jong)
                curSyl.jong = kept
                if (moved != "ㅎ") nxtSyl.cho = moved // the ㅎ half of ㄶ/ㅀ also just drops
                continue
            }

            // Before a CONSONANT: figure out what the batchim actually
            // sounds like there (cluster-ending-in-ㅎ, plain ㅎ, or its
            // representative stop/nasal/liquid per 음절의 끝소리 규칙).
            val hasH: Boolean
            val leftover: String
            when {
                jong in H_CLUSTER_LEFTOVER -> {
                    hasH = true; leftover = H_CLUSTER_LEFTOVER.getValue(jong)
                }
                jong == "ㅎ" -> {
                    hasH = true; leftover = ""
                }
                else -> {
                    hasH = false; leftover = FINAL_MAP[jong] ?: jong
                }
            }

            if (hasH && onset in ASPIRATE) {
                // 격음화: 좋다 -> 조타, 많고 -> 만코 (ㄶ/ㅀ's ㄴ/ㄹ leftover stays)
                curSyl.jong = leftover
                nxtSyl.cho = ASPIRATE.getValue(onset)
            } else if (hasH && onset == "ㄴ") {
                // 놓는 -> 논는, 않니 -> 안니; a ㅀ-leftover ㄹ instead
                // triggers the liquid rule below (옳니 -> 올리).
                curSyl.jong = if (leftover == "ㄴ" || leftover == "ㄹ") leftover else "ㄴ"
                if (curSyl.jong == "ㄹ") nxtSyl.cho = "ㄹ"
            } else if (!hasH && onset == "ㅎ" && leftover in NASALIZE_STOP) {
                curSyl.jong = "" // 각하 -> 가카 (coda merges into ㅎ as an aspirate)
                nxtSyl.cho = ASPIRATE.getValue(leftover)
            } else if (!hasH && onset == "ㄹ") {
                when {
                    leftover in NASALIZE_STOP -> {
                        curSyl.jong = NASALIZE_STOP.getValue(leftover) // 협력 -> 혐녁
                        nxtSyl.cho = "ㄴ"
                    }
                    leftover == "ㅁ" || leftover == "ㅇ" -> nxtSyl.cho = "ㄴ" // 담력 -> 담녁, 종로 -> 종노
                    leftover == "ㄴ" -> {
                        // 신라 -> 실라 (the regular case; Sino-Korean
                        // exceptions like 의견란 -> 의견난 aren't handled)
                        curSyl.jong = "ㄹ"
                        nxtSyl.cho = "ㄹ"
                    }
                }
            } else if (!hasH && (onset == "ㄴ" || onset == "ㅁ")) {
                if (leftover in NASALIZE_STOP) {
                    curSyl.jong = NASALIZE_STOP.getValue(leftover) // 국물 -> 궁물, 밥물 -> 밤물
                } else if (leftover == "ㄹ" && onset == "ㄴ") {
                    nxtSyl.cho = "ㄹ" // 칼날 -> 칼랄, 설날 -> 설랄
                }
            } else if (!hasH && onset in TENSIFY && (leftover == "ㄱ" || leftover == "ㄷ" || leftover == "ㅂ")) {
                // 경음화: 국밥 -> 국빱, 학교 -> 학꾜, 학생 -> 학쌩. Coda itself
                // doesn't change, only the following plain obstruent tenses.
                nxtSyl.cho = TENSIFY.getValue(onset)
            }
        }
    }

    private fun compose(cho: String, jung: String, jong: String): Char {
        val choIdx = CHO_INDEX.getValue(cho[0])
        val jungIdx = JUNG_INDEX.getValue(jung[0])
        val jongIdx = JONG_INDEX.getValue(jong)
        return (HANGUL_START + (choIdx * 21 + jungIdx) * 28 + jongIdx).toChar()
    }

    /**
     * Rewrite [text] into its standard spoken form (all rules above
     * applied), e.g. "옷이 좋아요" -> "오시 조아요", "읽었다" -> "일걷따".
     * This is what actually gets voiced.
     */
    fun textToPronunciation(text: String): String {
        val slots = parse(text)
        applyLocalVowelRules(slots)
        applyContextRules(slots)
        val out = StringBuilder()
        for (slot in slots) {
            when (slot) {
                is Slot.Other -> out.append(slot.ch)
                is Slot.Hangul -> {
                    var jong = slot.syl.jong
                    // Any batchim that survived to here is staying put, so
                    // display it exactly as it will be voiced: reduced to
                    // its representative sound if needed.
                    if (jong in CONSONANTS) jong = FINAL_MAP[jong] ?: jong
                    out.append(compose(slot.syl.cho, slot.syl.jung, jong))
                }
            }
        }
        return out.toString()
    }

    /** (cho, jung, jong) -> flat jamo list: batchim neutralisation + compound-vowel splitting. */
    private fun syllableToJamo(cho: String, jung: String, jong: String): List<String> {
        val a = mutableListOf(cho, jung)
        if (jong.isNotEmpty()) a.add(jong)
        if (a.last() in CONSONANTS) a[a.lastIndex] = FINAL_MAP[a.last()] ?: a.last()

        val b = mutableListOf<String>()
        for (j in a) (COMPOUND_VOWELS[j] ?: j).forEach { b.add(it.toString()) }
        val c = mutableListOf<String>()
        for (j in b) (VOWEL_MERGE[j] ?: j).forEach { c.add(it.toString()) }
        return c
    }

    /** One character's sample name(s), tagged with how build_audio should join them. */
    data class SampleGroup(val kind: String, val names: List<String>)

    /**
     * Groups samples so [AudioEngine] knows which adjacent ones are lobes of
     * the same syllable (a compound-vowel glide, or a vowel + bare sonorant-
     * coda recording) and should be crossfaded, vs a plain one-recording
     * syllable that needs no help. See korean_tts.py's `text_to_groups` for
     * the branch-by-branch derivation this mirrors exactly.
     */
    fun textToGroups(text: String): List<SampleGroup> {
        val groups = mutableListOf<SampleGroup>()
        fun pause() {
            if (groups.isEmpty() || groups.last().names != listOf(PAUSE)) {
                groups.add(SampleGroup("single", listOf(PAUSE)))
            }
        }

        val slots = parse(text)
        applyLocalVowelRules(slots)
        applyContextRules(slots)

        for (slot in slots) {
            if (slot !is Slot.Hangul) {
                pause()
                continue
            }
            val c = syllableToJamo(slot.syl.cho, slot.syl.jung, slot.syl.jong)
            if (c.any { it !in ROMAN }) {
                pause()
                continue
            }

            val last = c.last()
            when {
                c[0] == "ㅇ" && last !in SONORANTS && c.size == 2 + (if (last in CONSONANTS) 1 else 0) -> {
                    // ㅇ-onset, simple vowel, obstruent coda or none: one
                    // dedicated recording already covers this (압 -> "ab").
                    groups.add(SampleGroup("single", listOf(c.drop(1).joinToString("") { ROMAN.getValue(it) })))
                }
                c[0] == "ㅇ" && last in SONORANTS -> {
                    // ㅇ-onset + sonorant coda: size 3 = simple vowel
                    // (안 -> a+n), size 4 = compound vowel too (왕 -> o+a+ng).
                    val names = c.drop(1).map { ROMAN.getValue(it) }
                    groups.add(SampleGroup(if (c.size == 4) "diphthong" else "coda", names))
                }
                c[0] == "ㅇ" -> {
                    // ㅇ-onset, compound vowel (simple-vowel case handled above).
                    val names = listOf(ROMAN.getValue(c[1]), c.drop(2).joinToString("") { ROMAN.getValue(it) })
                    groups.add(SampleGroup("diphthong", names))
                }
                c.size == 2 && c[0] in CONSONANTS -> {
                    groups.add(SampleGroup("single", listOf(ROMAN.getValue(c[0]) + ROMAN.getValue(c[1]))))
                }
                c.size == 2 -> {
                    // Unreachable (c[0] is always in CONSONANTS); kept for parity with the Python port.
                    groups.add(SampleGroup("single", listOf(ROMAN.getValue(c[0]), ROMAN.getValue(c[1]))))
                }
                last in CONSONANTS && last !in SONORANTS -> {
                    // Real onset + obstruent coda: size 3 = simple vowel,
                    // already one recording per half (학 -> ha+ag). size 4 =
                    // compound vowel too (확 -> ho+ag), still a glide split.
                    val names = listOf(
                        ROMAN.getValue(c[0]) + ROMAN.getValue(c[1]),
                        ROMAN.getValue(c[c.size - 2]) + ROMAN.getValue(last),
                    )
                    groups.add(SampleGroup(if (c.size == 4) "diphthong" else "single", names))
                }
                last in SONORANTS && c.size == 4 -> {
                    // Real onset + compound vowel + sonorant coda (웜 -> u+eo+m).
                    groups.add(
                        SampleGroup(
                            "diphthong",
                            listOf(ROMAN.getValue(c[0]) + ROMAN.getValue(c[1]), ROMAN.getValue(c[2]), ROMAN.getValue(c[3])),
                        )
                    )
                }
                last in SONORANTS && c.size == 3 -> {
                    // Real onset + simple vowel + sonorant coda (반 -> ba+n).
                    groups.add(SampleGroup("coda", listOf(ROMAN.getValue(c[0]) + ROMAN.getValue(c[1]), ROMAN.getValue(c[2]))))
                }
                else -> {
                    // Real onset + compound vowel + no coda (과 -> go+a).
                    groups.add(SampleGroup("diphthong", listOf(ROMAN.getValue(c[0]) + ROMAN.getValue(c[1]), ROMAN.getValue(last))))
                }
            }
        }
        return groups
    }

    /** Flat sample-name sequence, same content as [textToGroups] but without the grouping. */
    fun textToSamples(text: String): List<String> = textToGroups(text).flatMap { it.names }
}
