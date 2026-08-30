package kr.koreantts.app

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.media.PlaybackParams
import android.os.Handler
import android.os.Looper

/**
 * Thin wrapper around AudioTrack for playing one fully-decoded PCM clip at a
 * time. MODE_STATIC is used since a TTS utterance is short and its full
 * length is known up front — simpler than streaming.
 */
object SpeechPlayer {
    private var track: AudioTrack? = null
    private var pendingDone: (() -> Unit)? = null
    private val mainHandler = Handler(Looper.getMainLooper())

    /**
     * Plays [samples] (mono 16-bit @ [AudioEngine.TARGET_RATE]) at [speed]x
     * and [volume] (0.0 mute .. 1.0 full, independent of the phone's system
     * media volume).
     *
     * Speed uses AudioTrack's own PlaybackParams with pitch pinned to 1.0,
     * so — unlike the desktop tts.py's --speed, which just resamples and
     * therefore shifts pitch along with speed — the voice doesn't get
     * higher/lower when sped up or down. Values are clamped to
     * [Prefs.MIN_SPEED]..[Prefs.MAX_SPEED] and [Prefs.MIN_VOLUME]..[Prefs.MAX_VOLUME].
     *
     * [onDone] always runs exactly once, on the main thread — whether
     * playback finishes naturally or gets cut short by a later [stop] or
     * [play] call, so a caller waiting on it (e.g. to close an activity)
     * never hangs.
     */
    fun play(samples: ShortArray, speed: Float = 1.0f, volume: Float = 1.0f, onDone: () -> Unit) {
        stop() // fires any previous caller's onDone before we take over

        if (samples.isEmpty()) {
            onDone()
            return
        }

        val newTrack = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(AudioEngine.TARGET_RATE)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build()
            )
            .setBufferSizeInBytes(samples.size * 2)
            .setTransferMode(AudioTrack.MODE_STATIC)
            .build()

        var finished = false
        fun finishOnce() {
            if (finished) return
            finished = true
            pendingDone = null
            mainHandler.post(onDone)
        }
        pendingDone = ::finishOnce

        newTrack.setNotificationMarkerPosition(samples.size)
        newTrack.setPlaybackPositionUpdateListener(object : AudioTrack.OnPlaybackPositionUpdateListener {
            override fun onMarkerReached(t: AudioTrack) = finishOnce()
            override fun onPeriodicNotification(t: AudioTrack) {}
        })

        newTrack.write(samples, 0, samples.size)
        track = newTrack

        val clampedSpeed = speed.coerceIn(Prefs.MIN_SPEED, Prefs.MAX_SPEED)
        if (clampedSpeed != 1.0f) {
            newTrack.playbackParams = PlaybackParams().setSpeed(clampedSpeed).setPitch(1.0f)
        }
        newTrack.setVolume(volume.coerceIn(Prefs.MIN_VOLUME, Prefs.MAX_VOLUME))

        newTrack.play()
    }

    /** Stops any current playback and fires its caller's onDone (see [play]). Safe to call when nothing is playing. */
    fun stop() {
        val done = pendingDone
        pendingDone = null
        track?.let {
            try {
                it.stop()
            } catch (_: IllegalStateException) {
                // already stopped
            }
            it.release()
        }
        track = null
        done?.invoke()
    }

    fun isPlaying(): Boolean = track?.playState == AudioTrack.PLAYSTATE_PLAYING
}
