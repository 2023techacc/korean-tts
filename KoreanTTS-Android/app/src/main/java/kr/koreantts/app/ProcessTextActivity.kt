package kr.koreantts.app

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Handles Android's ACTION_PROCESS_TEXT: whatever text the user had
 * selected in another app is in [Intent.EXTRA_PROCESS_TEXT]. This activity
 * is fully transparent (see Theme.KoreanTts.Transparent) and finishes
 * itself the moment playback ends, so the app the user was in is never
 * visibly interrupted — that's the entire "read selected text without
 * leaving the app" feature.
 *
 * We never call setResult() with a replacement string, since
 * EXTRA_PROCESS_TEXT_READONLY or not, reading aloud shouldn't edit the
 * source app's text.
 */
class ProcessTextActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val text = intent?.getCharSequenceExtra(Intent.EXTRA_PROCESS_TEXT)?.toString()?.trim()
        if (text.isNullOrEmpty()) {
            finish()
            return
        }

        lifecycleScope.launch {
            val result = withContext(Dispatchers.Default) {
                val groups = KoreanPhonology.textToGroups(text)
                AudioEngine.buildAudio(
                    assets,
                    groups,
                    gapMs = Prefs.getGapMs(this@ProcessTextActivity),
                    stopGapMs = Prefs.getStopGapMs(this@ProcessTextActivity),
                )
            }

            if (result.samples.isEmpty()) {
                Toast.makeText(this@ProcessTextActivity, R.string.error_empty, Toast.LENGTH_SHORT).show()
                finish()
                return@launch
            }

            Toast.makeText(this@ProcessTextActivity, "🔊", Toast.LENGTH_SHORT).show() // 🔊 minimal "something happened" cue
            SpeechPlayer.play(
                result.samples,
                speed = Prefs.getSpeed(this@ProcessTextActivity),
                volume = Prefs.getVolume(this@ProcessTextActivity),
            ) { finish() }
        }
    }

    override fun onStop() {
        super.onStop()
        // The user switched away (e.g. back to the app they selected text
        // in, or somewhere else entirely) — don't keep talking in the background.
        SpeechPlayer.stop()
    }
}
