package kr.koreantts.app

import android.content.Context

/**
 * App-wide settings: playback speed, pause/spacing length, and volume.
 * Shared between MainActivity and ProcessTextActivity so "settable in the
 * app" means once, not per-screen — including when reading a text
 * selection from another app.
 */
object Prefs {
    private const val FILE = "koreantts_prefs"

    private const val KEY_SPEED = "speed"
    const val DEFAULT_SPEED = 1.0f
    const val MIN_SPEED = 0.5f
    const val MAX_SPEED = 2.0f

    // Silence length at spaces/punctuation - AudioEngine.buildAudio's gapMs,
    // matching desktop tts.py's --gap (default 300ms there too).
    private const val KEY_GAP_MS = "gap_ms"
    const val DEFAULT_GAP_MS = 300
    const val MIN_GAP_MS = 0
    const val MAX_GAP_MS = 800

    // Per-track gain via AudioTrack.setVolume(), independent of the phone's
    // system media volume - this only scales what this app itself plays.
    private const val KEY_VOLUME = "volume"
    const val DEFAULT_VOLUME = 1.0f
    const val MIN_VOLUME = 0.0f
    const val MAX_VOLUME = 1.0f

    // Extra silence after a syllable ending in ㄱ/ㄷ/ㅂ (and their variants
    // ㅋ,ㄲ,ㅌ,etc, all neutralised to one of those three) - AudioEngine.
    // buildAudio's stopGapMs, matching desktop tts.py's --stop-gap.
    private const val KEY_STOP_GAP_MS = "stop_gap_ms"
    const val DEFAULT_STOP_GAP_MS = AudioEngine.DEFAULT_STOP_GAP_MS
    const val MIN_STOP_GAP_MS = 0
    const val MAX_STOP_GAP_MS = 200

    fun getSpeed(context: Context): Float {
        val prefs = context.getSharedPreferences(FILE, Context.MODE_PRIVATE)
        return prefs.getFloat(KEY_SPEED, DEFAULT_SPEED).coerceIn(MIN_SPEED, MAX_SPEED)
    }

    fun setSpeed(context: Context, speed: Float) {
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE)
            .edit()
            .putFloat(KEY_SPEED, speed.coerceIn(MIN_SPEED, MAX_SPEED))
            .apply()
    }

    fun getGapMs(context: Context): Int {
        val prefs = context.getSharedPreferences(FILE, Context.MODE_PRIVATE)
        return prefs.getInt(KEY_GAP_MS, DEFAULT_GAP_MS).coerceIn(MIN_GAP_MS, MAX_GAP_MS)
    }

    fun setGapMs(context: Context, gapMs: Int) {
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE)
            .edit()
            .putInt(KEY_GAP_MS, gapMs.coerceIn(MIN_GAP_MS, MAX_GAP_MS))
            .apply()
    }

    fun getVolume(context: Context): Float {
        val prefs = context.getSharedPreferences(FILE, Context.MODE_PRIVATE)
        return prefs.getFloat(KEY_VOLUME, DEFAULT_VOLUME).coerceIn(MIN_VOLUME, MAX_VOLUME)
    }

    fun setVolume(context: Context, volume: Float) {
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE)
            .edit()
            .putFloat(KEY_VOLUME, volume.coerceIn(MIN_VOLUME, MAX_VOLUME))
            .apply()
    }

    fun getStopGapMs(context: Context): Int {
        val prefs = context.getSharedPreferences(FILE, Context.MODE_PRIVATE)
        return prefs.getInt(KEY_STOP_GAP_MS, DEFAULT_STOP_GAP_MS).coerceIn(MIN_STOP_GAP_MS, MAX_STOP_GAP_MS)
    }

    fun setStopGapMs(context: Context, stopGapMs: Int) {
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE)
            .edit()
            .putInt(KEY_STOP_GAP_MS, stopGapMs.coerceIn(MIN_STOP_GAP_MS, MAX_STOP_GAP_MS))
            .apply()
    }
}
