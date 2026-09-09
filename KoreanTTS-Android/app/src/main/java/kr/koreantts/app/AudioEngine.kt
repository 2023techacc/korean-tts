package kr.koreantts.app

import android.content.res.AssetManager
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Loads the bundled .wav samples (in assets/sound) and assembles them into
 * one PCM track per [KoreanPhonology.textToGroups] result: loudness-matched,
 * silence-trimmed, crossfaded within each syllable group, with a bare
 * sonorant-coda recording (ㄴㄹㅁㅇ) shortened to a quick closure first.
 *
 * Direct port of korean_tts.py's audio pipeline — same constants, same
 * order of operations. See that file for the measurements behind the
 * default numbers (RMS range across the 268 samples, before/after crossfade
 * durations, etc).
 */
object AudioEngine {

    const val TARGET_RATE = 44100
    private const val SAMPLE_WIDTH = 2 // 16-bit signed

    // Loudness varies ~45x across the bundled samples; these tune the RMS
    // matching in normalizeLoudness(). Values (and the "pretty high" bump
    // from the original 2200/6.0/30000) picked by measuring, for each
    // candidate target, how many of the 268 samples land >10-30% short of
    // it after gain+peak capping - 3500/8.0/32000 is +4dB louder than the
    // original while only the 3% quietest outliers fall notably short (and
    // even those still land at 40%+ of target, not silent).
    private const val NORMALIZE_TARGET_RMS = 3500.0
    private const val NORMALIZE_MAX_GAIN = 8.0
    private const val NORMALIZE_PEAK_LIMIT = 32000
    private const val SILENCE_THRESHOLD = 250

    // How much of the shorter of two adjacent clips to overlap, as a
    // fraction of its length: generous for a diphthong (blending vowel
    // qualities together is correct), lighter for a sonorant coda (don't
    // swallow the consonant's identity).
    private val CROSSFADE_FRACTION = mapOf("diphthong" to 0.50, "coda" to 0.25)
    private const val CROSSFADE_MIN_MS = 20
    private const val CROSSFADE_MAX_MS = 150

    // The bare consonant recordings standing in for a sonorant batchim run
    // 150-230ms on their own (recorded as a full mini-syllable, not a quick
    // closure) — truncated before crossfading kicks in. These names are
    // unambiguous: an onset+vowel sample is always 2+ romanised letters
    // glued together (e.g. "ba", "go"), so a bare "n"/"l"/"m"/"ng" only ever
    // occurs in coda position.
    private val CODA_TAILS = setOf("n", "l", "m", "ng")
    private const val CODA_MAX_MS = 120
    private const val CODA_TAIL_FADE_MS = 20

    // Closed syllables ending in an unreleased obstruent stop (representative
    // ㄱ/ㄷ/ㅂ after 음절의 끝소리 규칙 - covers ㅋ/ㄲ, ㅅ/ㅆ/ㅈ/ㅊ/ㅌ/ㅎ, ㅍ
    // too) run straight into the next syllable with zero gap, same as any
    // other boundary. A real stop closure has some hold time before release,
    // so that reads as rushed. Detectable from the sample name alone: such a
    // syllable's last sample is always a "vowel+consonant" romanisation
    // ending in exactly g/d/b (e.g. "ag", "ug", "ad") - the sole exception,
    // the sonorant tail "ng", is excluded via CODA_TAILS.
    private val STOP_CODA_ENDINGS = setOf('g', 'd', 'b')
    const val DEFAULT_STOP_GAP_MS = 40

    private fun endsInStopCoda(name: String): Boolean =
        name.isNotEmpty() && name !in CODA_TAILS && name.last() in STOP_CODA_ENDINGS

    // Every romanized base piece name -> the hex codepoint of a real
    // character that produces exactly that recording (e.g. "ga" -> "ac00"
    // == 가), used only when a bank's bank.json sets "hex_pieces": true (see
    // pieceFilename below). Generated from korean_tts.py's
    // PIECE_REPRESENTATIVE_CHARS - a fixed, verified table, not re-derived
    // here, so there's no risk of the two ports drifting apart. "n"/"l"/
    // "m"/"ng" get a "_tail" suffix: those four are recorded SEPARATELY
    // from the ordinary onset+vowel piece that happens to share the same
    // representative character (e.g. "n"'s 느 is also "neu"'s 느, but as a
    // trimmed coda-closure take, not the same file) - without the suffix,
    // hex-naming would silently collapse them onto one file.
    private val PIECE_HEX_NAMES: Map<String, String> = mapOf(
        "a" to "c544", "ab" to "c555", "ad" to "c54b", "ae" to "c560",
        "aeb" to "c571", "aed" to "c567", "aeg" to "c561", "ag" to "c545",
        "ba" to "bc14", "bae" to "bc30", "bba" to "be60", "bbae" to "be7c",
        "bbeo" to "bed0", "bbeu" to "c058", "bbi" to "c090", "bbo" to "bf40",
        "bbu" to "bfcc", "bbya" to "be98", "bbyae" to "beb4", "bbyeo" to "bf08",
        "bbyo" to "bfb0", "bbyu" to "c03c", "beo" to "bc84", "beu" to "be0c",
        "bi" to "be44", "bo" to "bcf4", "bu" to "bd80", "bya" to "bc4c",
        "byae" to "bc68", "byeo" to "bcbc", "byo" to "bd64", "byu" to "bdf0",
        "cha" to "cc28", "chae" to "cc44", "cheo" to "cc98", "cheu" to "ce20",
        "chi" to "ce58", "cho" to "cd08", "chu" to "cd94", "da" to "b2e4",
        "dae" to "b300", "dda" to "b530", "ddae" to "b54c", "ddeo" to "b5a0",
        "ddeu" to "b728", "ddi" to "b760", "ddo" to "b610", "ddu" to "b69c",
        "ddya" to "b568", "ddyae" to "b584", "ddyeo" to "b5d8", "ddyo" to "b680",
        "ddyu" to "b70c", "deo" to "b354", "deu" to "b4dc", "di" to "b514",
        "do" to "b3c4", "du" to "b450", "dya" to "b31c", "dyae" to "b338",
        "dyeo" to "b38c", "dyo" to "b434", "dyu" to "b4c0", "eo" to "c5b4",
        "eob" to "c5c5", "eod" to "c5bb", "eog" to "c5b5", "eu" to "c73c",
        "eub" to "c74d", "eud" to "c743", "eug" to "c73d", "ga" to "ac00",
        "gae" to "ac1c", "geo" to "ac70", "geu" to "adf8", "gga" to "ae4c",
        "ggae" to "ae68", "ggeo" to "aebc", "ggeu" to "b044", "ggi" to "b07c",
        "ggo" to "af2c", "ggu" to "afb8", "ggya" to "ae84", "ggyae" to "aea0",
        "ggyeo" to "aef4", "ggyo" to "af9c", "ggyu" to "b028", "gi" to "ae30",
        "go" to "ace0", "gu" to "ad6c", "gya" to "ac38", "gyae" to "ac54",
        "gyeo" to "aca8", "gyo" to "ad50", "gyu" to "addc", "ha" to "d558",
        "hae" to "d574", "heo" to "d5c8", "heu" to "d750", "hi" to "d788",
        "ho" to "d638", "hu" to "d6c4", "hya" to "d590", "hyae" to "d5ac",
        "hyeo" to "d600", "hyo" to "d6a8", "hyu" to "d734", "i" to "c774",
        "ib" to "c785", "id" to "c77b", "ig" to "c775", "ja" to "c790",
        "jae" to "c7ac", "jeo" to "c800", "jeu" to "c988", "ji" to "c9c0",
        "jja" to "c9dc", "jjae" to "c9f8", "jjeo" to "ca4c", "jjeu" to "cbd4",
        "jji" to "cc0c", "jjo" to "cabc", "jju" to "cb48", "jo" to "c870",
        "ju" to "c8fc", "ka" to "ce74", "kae" to "ce90", "keo" to "cee4",
        "keu" to "d06c", "ki" to "d0a4", "ko" to "cf54", "ku" to "cfe0",
        "kya" to "ceac", "kyae" to "cec8", "kyeo" to "cf1c", "kyo" to "cfc4",
        "kyu" to "d050", "l" to "b974_tail", "la" to "b77c", "lae" to "b798",
        "leo" to "b7ec", "leu" to "b974", "li" to "b9ac", "lo" to "b85c",
        "lu" to "b8e8", "lya" to "b7b4", "lyae" to "b7d0", "lyeo" to "b824",
        "lyo" to "b8cc", "lyu" to "b958", "m" to "bbc0_tail", "ma" to "b9c8",
        "mae" to "b9e4", "meo" to "ba38", "meu" to "bbc0", "mi" to "bbf8",
        "mo" to "baa8", "mu" to "bb34", "mya" to "ba00", "myae" to "ba1c",
        "myeo" to "ba70", "myo" to "bb18", "myu" to "bba4", "n" to "b290_tail",
        "na" to "b098", "nae" to "b0b4", "neo" to "b108", "neu" to "b290",
        "ng" to "c751_tail", "ni" to "b2c8", "no" to "b178", "nu" to "b204",
        "nya" to "b0d0", "nyae" to "b0ec", "nyeo" to "b140", "nyo" to "b1e8",
        "nyu" to "b274", "o" to "c624", "ob" to "c635", "od" to "c62b",
        "og" to "c625", "pa" to "d30c", "pae" to "d328", "peo" to "d37c",
        "peu" to "d504", "pi" to "d53c", "po" to "d3ec", "pu" to "d478",
        "pya" to "d344", "pyae" to "d360", "pyeo" to "d3b4", "pyo" to "d45c",
        "pyu" to "d4e8", "sa" to "c0ac", "sae" to "c0c8", "seo" to "c11c",
        "seu" to "c2a4", "si" to "c2dc", "so" to "c18c", "ssa" to "c2f8",
        "ssae" to "c314", "sseo" to "c368", "sseu" to "c4f0", "ssi" to "c528",
        "sso" to "c3d8", "ssu" to "c464", "ssya" to "c330", "ssyae" to "c34c",
        "ssyeo" to "c3a0", "ssyo" to "c448", "ssyu" to "c4d4", "su" to "c218",
        "sya" to "c0e4", "syae" to "c100", "syeo" to "c154", "syo" to "c1fc",
        "syu" to "c288", "ta" to "d0c0", "tae" to "d0dc", "teo" to "d130",
        "teu" to "d2b8", "ti" to "d2f0", "to" to "d1a0", "tu" to "d22c",
        "tya" to "d0f8", "tyae" to "d114", "tyeo" to "d168", "tyo" to "d210",
        "tyu" to "d29c", "u" to "c6b0", "ub" to "c6c1", "ud" to "c6b7",
        "ug" to "c6b1", "ya" to "c57c", "yab" to "c58d", "yad" to "c583",
        "yae" to "c598", "yaeb" to "c5a9", "yaed" to "c59f", "yaeg" to "c599",
        "yag" to "c57d", "yeo" to "c5ec", "yeob" to "c5fd", "yeod" to "c5f3",
        "yeog" to "c5ed", "yo" to "c694", "yob" to "c6a5", "yod" to "c69b",
        "yog" to "c695", "yu" to "c720", "yub" to "c731", "yud" to "c727",
        "yug" to "c721"
    )

    /** The actual asset filename (no extension) for a base romanized piece
     * name - `name` itself unless the bank has "hex_pieces": true, in
     * which case it's PIECE_HEX_NAMES[name]. Mirrors korean_tts.py's
     * piece_filename() exactly. */
    private fun pieceFilename(name: String, hexPieces: Boolean): String {
        if (!hexPieces) return name
        return PIECE_HEX_NAMES[name] ?: name
    }

    /** sound/<voice>/bank.json's "settings.hex_pieces" field, or false if
     * missing/unreadable/malformed - same fallback bankType() uses for
     * "type". */
    private fun bankHexPieces(assets: AssetManager, voice: String): Boolean {
        return try {
            val text = assets.open("sound/$voice/bank.json").bufferedReader().use { it.readText() }
            org.json.JSONObject(text).optJSONObject("settings")?.optBoolean("hex_pieces", false) ?: false
        } catch (e: Exception) {
            false
        }
    }

    class AudioException(message: String) : Exception(message)

    /** Result of [buildAudio]: the assembled track plus any sample names that had no matching file. */
    data class BuildResult(val samples: ShortArray, val missing: List<String>)

    // ------------------------------------------------------
    // Multi-voice support
    // ------------------------------------------------------
    //
    // Every voice, including the original recordings, is a same-named
    // subfolder under assets/sound/ (assets/sound/default/ga.wav,
    // assets/sound/narrator2/ga.wav, ...) - see korean_tts.py's matching
    // list_voices/voice_dir for the full rationale.

    const val DEFAULT_VOICE = "default"

    private fun assetPath(voice: String, name: String): String = "sound/$voice/$name.wav"

    // Bank types this app actually knows how to play - see korean_tts.py's
    // "Sound banks" section for the full picture (bank.json, other types
    // like "full-syllable"). Anything else is filtered out of listVoices()
    // below rather than risking a crash or wrong assembly trying to play
    // it - a bank of an unsupported type just never appears in the picker.
    private val KNOWN_BANK_TYPES = setOf("pieces")

    /** sound/<voice>/bank.json's "type" field, or "pieces" if the file is
     * missing/unreadable/malformed - same fallback korean_tts.py's
     * load_bank_manifest() uses. org.json is part of the Android platform
     * SDK (no Gradle dependency needed), matching this app's zero third-
     * party-dependency approach everywhere else. */
    private fun bankType(assets: AssetManager, voice: String): String {
        return try {
            val text = assets.open("sound/$voice/bank.json").bufferedReader().use { it.readText() }
            org.json.JSONObject(text).optString("type", "pieces")
        } catch (e: Exception) {
            "pieces"
        }
    }

    /**
     * Voices bundled in assets/sound/: any subfolder name whose bank.json
     * (if present) declares a type this app knows how to play, [DEFAULT_VOICE]
     * first. AssetManager.list() on a leaf file returns an empty array, so -
     * since every real sample name always ends in ".wav" and no voice folder
     * ever will - anything listed that ISN'T a ".wav" name is a voice folder,
     * with no need for a separate is-this-a-directory check.
     */
    fun listVoices(assets: AssetManager): List<String> {
        val entries = try {
            assets.list("sound") ?: emptyArray()
        } catch (e: java.io.IOException) {
            emptyArray()
        }
        val voices = entries.filterNot { it.endsWith(".wav", ignoreCase = true) }
            .filter { bankType(assets, it) in KNOWN_BANK_TYPES }
            .toMutableList()
        if (voices.remove(DEFAULT_VOICE)) voices.add(0, DEFAULT_VOICE)
        return voices.ifEmpty { listOf(DEFAULT_VOICE) }
    }

    // ------------------------------------------------------
    // WAV loading
    // ------------------------------------------------------

    private class WavData(val channels: Int, val sampleRate: Int, val bitsPerSample: Int, val pcm: ByteArray)

    /** Minimal RIFF/WAVE chunk scanner: robust to extra chunks before "data", unlike assuming a fixed 44-byte header. */
    private fun parseWav(bytes: ByteArray): WavData {
        fun u32(off: Int) = (bytes[off].toInt() and 0xFF) or
            ((bytes[off + 1].toInt() and 0xFF) shl 8) or
            ((bytes[off + 2].toInt() and 0xFF) shl 16) or
            ((bytes[off + 3].toInt() and 0xFF) shl 24)
        fun u16(off: Int) = (bytes[off].toInt() and 0xFF) or ((bytes[off + 1].toInt() and 0xFF) shl 8)
        fun tag(off: Int) = String(bytes, off, 4, Charsets.US_ASCII)

        require(bytes.size >= 12 && tag(0) == "RIFF" && tag(8) == "WAVE") { "not a RIFF/WAVE file" }

        var pos = 12
        var channels = -1
        var sampleRate = -1
        var bitsPerSample = -1
        var pcm: ByteArray? = null

        while (pos + 8 <= bytes.size) {
            val chunkId = tag(pos)
            val chunkSize = u32(pos + 4)
            val body = pos + 8
            when (chunkId) {
                "fmt " -> {
                    channels = u16(body + 2)
                    sampleRate = u32(body + 4)
                    bitsPerSample = u16(body + 14)
                }
                "data" -> {
                    pcm = bytes.copyOfRange(body, min(body + chunkSize, bytes.size))
                }
            }
            pos = body + chunkSize + (chunkSize and 1) // chunks are word-aligned; skip the pad byte
        }

        if (channels < 0 || pcm == null) throw AudioException("malformed WAV (missing fmt/data chunk)")
        return WavData(channels, sampleRate, bitsPerSample, pcm!!)
    }

    private fun rms(samples: ShortArray): Double {
        if (samples.isEmpty()) return 0.0
        var sumSq = 0.0 // Double accumulation: a Short^2 sum over a whole clip overflows Int/Long-of-Int fast
        for (x in samples) sumSq += x.toDouble() * x.toDouble()
        return sqrt(sumSq / samples.size)
    }

    private fun trimSilence(samples: ShortArray, threshold: Int = SILENCE_THRESHOLD): ShortArray {
        var start = 0
        while (start < samples.size && kotlin.math.abs(samples[start].toInt()) < threshold) start++
        var end = samples.size
        while (end > start && kotlin.math.abs(samples[end - 1].toInt()) < threshold) end--
        return if (start == 0 && end == samples.size) samples else samples.copyOfRange(start, end)
    }

    private fun normalizeLoudness(
        samples: ShortArray,
        targetRms: Double = NORMALIZE_TARGET_RMS,
        maxGain: Double = NORMALIZE_MAX_GAIN,
        peakLimit: Int = NORMALIZE_PEAK_LIMIT,
    ): ShortArray {
        if (samples.isEmpty()) return samples
        val r = rms(samples)
        if (r <= 0.0) return samples

        var gain = min(targetRms / r, maxGain)
        val peak = samples.maxOf { max(it.toInt(), -it.toInt()) }
        if (peak > 0 && peak * gain > peakLimit) gain = peakLimit / peak.toDouble()

        if (kotlin.math.abs(gain - 1.0) < 0.01) return samples
        return ShortArray(samples.size) { i ->
            (samples[i] * gain).toInt().coerceIn(-32768, 32767).toShort()
        }
    }

    /**
     * Load one sample for [voice] (see assetPath) from assets as mono
     * 16-bit @ TARGET_RATE, trimmed and loudness-matched. Returns null if
     * the asset doesn't exist.
     */
    private fun readSample(assets: AssetManager, name: String, normalize: Boolean, voice: String): ShortArray? {
        val bytes = try {
            assets.open(assetPath(voice, name)).use { it.readBytes() }
        } catch (e: java.io.IOException) {
            return null
        }

        val wav = parseWav(bytes)
        if (wav.bitsPerSample != 16) {
            throw AudioException("$name.wav: expected 16-bit audio, got ${wav.bitsPerSample}-bit")
        }

        // WAV PCM is little-endian; unpack pairs of bytes into signed 16-bit samples.
        val frameCount = wav.pcm.size / 2
        var samples = ShortArray(frameCount) { i ->
            ((wav.pcm[i * 2].toInt() and 0xFF) or (wav.pcm[i * 2 + 1].toInt() shl 8)).toShort()
        }

        // The bundled sound/ folder mixes mono and stereo files; downmix to mono.
        if (wav.channels > 1) {
            val ch = wav.channels
            val usable = samples.size - (samples.size % ch)
            samples = ShortArray(usable / ch) { i ->
                var sum = 0
                for (c in 0 until ch) sum += samples[i * ch + c]
                (sum / ch).toShort()
            }
        }

        // All bundled samples are already 44100Hz; this only guards against an odd file being dropped in.
        if (wav.sampleRate != TARGET_RATE && samples.isNotEmpty()) {
            val n = max(1, samples.size.toLong() * TARGET_RATE / wav.sampleRate).toInt()
            val last = samples.size - 1
            samples = ShortArray(n) { i -> samples[min(last, (i.toLong() * wav.sampleRate / TARGET_RATE).toInt())] }
        }

        samples = trimSilence(samples)
        if (normalize) samples = normalizeLoudness(samples)
        return samples
    }

    // ------------------------------------------------------
    // Assembly: fades, coda shortening, crossfade
    // ------------------------------------------------------

    private fun applyFade(samples: ShortArray, fadeLen: Int) {
        val len = min(fadeLen, samples.size / 2)
        for (i in 0 until len) {
            val factor = i.toDouble() / len
            samples[i] = (samples[i] * factor).toInt().toShort()
            samples[samples.size - 1 - i] = (samples[samples.size - 1 - i] * factor).toInt().toShort()
        }
    }

    private fun shortenCoda(samples: ShortArray, maxMs: Int = CODA_MAX_MS, fadeMs: Int = CODA_TAIL_FADE_MS): ShortArray {
        val maxLen = TARGET_RATE * maxMs / 1000
        if (samples.size <= maxLen) return samples
        val cut = samples.copyOfRange(0, maxLen)
        val fadeLen = min(TARGET_RATE * fadeMs / 1000, cut.size / 2)
        for (i in 0 until fadeLen) {
            val factor = i.toDouble() / fadeLen
            val idx = cut.size - 1 - i
            cut[idx] = (cut[idx] * factor).toInt().toShort()
        }
        return cut
    }

    private fun overlapLen(kind: String, lenA: Int, lenB: Int): Int {
        val fraction = CROSSFADE_FRACTION[kind] ?: 0.0
        if (fraction <= 0.0 || lenA == 0 || lenB == 0) return 0
        val shorter = min(lenA, lenB)
        val lo = TARGET_RATE * CROSSFADE_MIN_MS / 1000
        val hi = TARGET_RATE * CROSSFADE_MAX_MS / 1000
        var ov = (shorter * fraction).toInt()
        ov = max(lo, ov)
        ov = min(ov, hi)
        ov = min(ov, shorter - 1)
        return max(0, ov)
    }

    /** Equal-power overlap-add join: shorter than concatenation, and the seam sounds continuous. */
    private fun crossfadeJoin(chunks: List<ShortArray>, kind: String): ShortArray {
        var result = chunks[0]
        for (i in 1 until chunks.size) {
            val nxt = chunks[i]
            var ov = overlapLen(kind, chunks[i - 1].size, nxt.size)
            ov = min(ov, min(result.size, nxt.size))
            if (ov <= 0) {
                result += nxt
                continue
            }
            val merged = result.copyOf(result.size + nxt.size - ov)
            val base = result.size - ov
            for (k in 0 until ov) {
                val t = (k + 1).toDouble() / (ov + 1)
                val fadeOut = cos(t * Math.PI / 2)
                val fadeIn = sin(t * Math.PI / 2)
                val mixed = result[base + k] * fadeOut + nxt[k] * fadeIn
                merged[base + k] = mixed.toInt().coerceIn(-32768, 32767).toShort()
            }
            System.arraycopy(nxt, ov, merged, base + ov, nxt.size - ov)
            result = merged
        }
        return result
    }

    // ------------------------------------------------------
    // Public entry point
    // ------------------------------------------------------

    /**
     * Concatenate grouped samples (from [KoreanPhonology.textToGroups]) into
     * one mono 16-bit @ 44100Hz PCM track.
     */
    /**
     * [stopGapMs] inserts extra silence after a syllable ending in an
     * obstruent stop batchim (ㄱ/ㄷ/ㅂ, see [endsInStopCoda]) and before the
     * next syllable - everywhere else, adjacent syllables still run
     * together with no gap. Pass 0 to disable.
     */
    fun buildAudio(
        assets: AssetManager,
        groups: List<KoreanPhonology.SampleGroup>,
        gapMs: Int = 300,
        fadeMs: Int = 5,
        normalize: Boolean = true,
        crossfade: Boolean = true,
        stopGapMs: Int = DEFAULT_STOP_GAP_MS,
        voice: String = DEFAULT_VOICE,
    ): BuildResult {
        val out = ArrayList<Short>(4096)
        val gap = ShortArray(TARGET_RATE * gapMs / 1000) // silence
        val stopGap = ShortArray(TARGET_RATE * stopGapMs / 1000)
        val fadeLen = TARGET_RATE * fadeMs / 1000
        val missing = mutableListOf<String>()
        val rawCache = HashMap<String, ShortArray?>()
        var prevEndsInStop = false
        val hexPieces = bankHexPieces(assets, voice)

        fun loadRaw(fileName: String): ShortArray? =
            rawCache.getOrPut(fileName) { readSample(assets, fileName, normalize, voice) }

        for (group in groups) {
            if (group.names == listOf(KoreanPhonology.PAUSE)) {
                for (s in gap) out.add(s)
                prevEndsInStop = false
                continue
            }

            if (prevEndsInStop && stopGapMs > 0) {
                for (s in stopGap) out.add(s)
            }

            val fileNames = group.names.map { pieceFilename(it, hexPieces) }

            val chunks = mutableListOf<ShortArray>()
            for (i in group.names.indices) {
                val name = group.names[i]
                val fileName = fileNames[i]
                val raw = loadRaw(fileName)
                if (raw == null) {
                    missing.add(fileName)
                    continue
                }
                var chunk = raw.copyOf() // copy: about to be mutated
                if (i > 0 && name in CODA_TAILS) chunk = shortenCoda(chunk) // a batchim, not this syllable's onset
                chunks.add(chunk)
            }
            if (chunks.isEmpty()) continue

            var combined = if (crossfade && chunks.size > 1) {
                crossfadeJoin(chunks, group.kind)
            } else {
                chunks[0]
            }
            if (chunks.size > 1 && !crossfade) {
                for (extra in chunks.drop(1)) combined += extra
            }

            if (fadeLen > 0) applyFade(combined, fadeLen)
            for (s in combined) out.add(s)
            prevEndsInStop = endsInStopCoda(group.names.last())
        }

        return BuildResult(out.toShortArray(), missing)
    }

    // ------------------------------------------------------
    // WAV export (for the "save as .wav" / share feature)
    // ------------------------------------------------------

    fun toWavBytes(samples: ShortArray): ByteArray {
        val dataSize = samples.size * 2
        val buf = java.nio.ByteBuffer.allocate(44 + dataSize).order(java.nio.ByteOrder.LITTLE_ENDIAN)
        buf.put("RIFF".toByteArray(Charsets.US_ASCII))
        buf.putInt(36 + dataSize)
        buf.put("WAVE".toByteArray(Charsets.US_ASCII))
        buf.put("fmt ".toByteArray(Charsets.US_ASCII))
        buf.putInt(16) // PCM fmt chunk size
        buf.putShort(1.toShort()) // AudioFormat = PCM
        buf.putShort(1.toShort()) // channels = mono
        buf.putInt(TARGET_RATE)
        buf.putInt(TARGET_RATE * SAMPLE_WIDTH) // byte rate
        buf.putShort(SAMPLE_WIDTH.toShort()) // block align
        buf.putShort(16.toShort()) // bits per sample
        buf.put("data".toByteArray(Charsets.US_ASCII))
        buf.putInt(dataSize)
        for (s in samples) buf.putShort(s)
        return buf.array()
    }

    fun durationSeconds(samples: ShortArray): Double = samples.size.toDouble() / TARGET_RATE
}