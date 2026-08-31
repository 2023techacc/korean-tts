package kr.koreantts.app

import android.content.Intent
import android.os.Bundle
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kr.koreantts.app.databinding.ActivityMainBinding
import java.io.File

/**
 * Standalone screen: type Korean text, play it, peek at the actual
 * pronunciation, or save it as a .wav to share. Same feature set as the
 * companion desktop project's tts.py CLI, built on the same engine
 * (KoreanPhonology + AudioEngine) as [ProcessTextActivity].
 */
class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private var pronunciationShown = false
    private var speed = Prefs.DEFAULT_SPEED
    private var gapMs = Prefs.DEFAULT_GAP_MS
    private var volume = Prefs.DEFAULT_VOLUME
    private var stopGapMs = Prefs.DEFAULT_STOP_GAP_MS
    private var voice = AudioEngine.DEFAULT_VOICE

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val voices = AudioEngine.listVoices(assets)
        voice = Prefs.getVoice(this).takeIf { it in voices } ?: AudioEngine.DEFAULT_VOICE
        binding.voiceSpinner.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, voices)
        binding.voiceSpinner.setSelection(voices.indexOf(voice))
        binding.voiceSpinner.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: android.view.View?, position: Int, id: Long) {
                voice = voices[position]
                Prefs.setVoice(this@MainActivity, voice)
            }
            override fun onNothingSelected(parent: AdapterView<*>?) {}
        }

        speed = Prefs.getSpeed(this)
        binding.speedSlider.value = speed
        binding.speedLabel.text = getString(R.string.label_speed, speed)
        binding.speedSlider.addOnChangeListener { _, value, _ ->
            speed = value
            binding.speedLabel.text = getString(R.string.label_speed, speed)
            Prefs.setSpeed(this, speed)
        }

        gapMs = Prefs.getGapMs(this)
        binding.gapSlider.value = gapMs.toFloat()
        binding.gapLabel.text = getString(R.string.label_gap, gapMs)
        binding.gapSlider.addOnChangeListener { _, value, _ ->
            gapMs = value.toInt()
            binding.gapLabel.text = getString(R.string.label_gap, gapMs)
            Prefs.setGapMs(this, gapMs)
        }

        volume = Prefs.getVolume(this)
        binding.volumeSlider.value = volume * 100f
        binding.volumeLabel.text = getString(R.string.label_volume, (volume * 100).toInt())
        binding.volumeSlider.addOnChangeListener { _, value, _ ->
            volume = value / 100f
            binding.volumeLabel.text = getString(R.string.label_volume, value.toInt())
            Prefs.setVolume(this, volume)
        }

        stopGapMs = Prefs.getStopGapMs(this)
        binding.stopGapSlider.value = stopGapMs.toFloat()
        binding.stopGapLabel.text = getString(R.string.label_stop_gap, stopGapMs)
        binding.stopGapSlider.addOnChangeListener { _, value, _ ->
            stopGapMs = value.toInt()
            binding.stopGapLabel.text = getString(R.string.label_stop_gap, stopGapMs)
            Prefs.setStopGapMs(this, stopGapMs)
        }

        binding.btnPlay.setOnClickListener { onPlay() }
        binding.btnStop.setOnClickListener { SpeechPlayer.stop() }
        binding.btnPronunciation.setOnClickListener { onTogglePronunciation() }
        binding.btnSave.setOnClickListener { onSave() }
    }

    override fun onStop() {
        super.onStop()
        SpeechPlayer.stop()
    }

    private fun currentText(): String = binding.textInput.text?.toString()?.trim().orEmpty()

    private fun onTogglePronunciation() {
        val text = currentText()
        if (text.isEmpty()) return
        pronunciationShown = !pronunciationShown
        if (pronunciationShown) {
            binding.pronunciationLabel.text = getString(R.string.label_pronunciation, KoreanPhonology.textToPronunciation(text))
            binding.pronunciationLabel.visibility = android.view.View.VISIBLE
        } else {
            binding.pronunciationLabel.visibility = android.view.View.GONE
        }
    }

    private fun onPlay() {
        val text = currentText()
        if (text.isEmpty()) return

        lifecycleScope.launch {
            val result = withContext(Dispatchers.Default) {
                val groups = KoreanPhonology.textToGroups(text)
                AudioEngine.buildAudio(assets, groups, gapMs = gapMs, stopGapMs = stopGapMs, voice = voice)
            }
            if (result.samples.isEmpty()) {
                Toast.makeText(this@MainActivity, R.string.error_empty, Toast.LENGTH_SHORT).show()
                return@launch
            }
            if (result.missing.isNotEmpty()) {
                Toast.makeText(
                    this@MainActivity,
                    getString(R.string.error_missing_samples, result.missing.distinct().size),
                    Toast.LENGTH_SHORT,
                ).show()
            }
            SpeechPlayer.play(result.samples, speed = speed, volume = volume) {}
        }
    }

    private fun onSave() {
        val text = currentText()
        if (text.isEmpty()) return

        lifecycleScope.launch {
            val (result, file) = withContext(Dispatchers.Default) {
                val groups = KoreanPhonology.textToGroups(text)
                val built = AudioEngine.buildAudio(assets, groups, gapMs = gapMs, stopGapMs = stopGapMs, voice = voice)
                val dir = File(cacheDir, "tts").apply { mkdirs() }
                val outFile = File(dir, "tts_${System.currentTimeMillis()}.wav")
                if (built.samples.isNotEmpty()) outFile.writeBytes(AudioEngine.toWavBytes(built.samples))
                built to outFile
            }

            if (result.samples.isEmpty()) {
                Toast.makeText(this@MainActivity, R.string.error_empty, Toast.LENGTH_SHORT).show()
                return@launch
            }

            val uri = FileProvider.getUriForFile(this@MainActivity, "$packageName.fileprovider", file)
            val shareIntent = Intent(Intent.ACTION_SEND).apply {
                type = "audio/wav"
                putExtra(Intent.EXTRA_STREAM, uri)
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            startActivity(Intent.createChooser(shareIntent, getString(R.string.btn_save)))
        }
    }
}
