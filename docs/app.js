import { textToGroups, textToPronunciation } from "./korean-phonology.js";
import { buildAudio, toWavBytes, DEFAULT_STOP_GAP_MS } from "./korean-audio.js";

const SETTINGS_KEY = "koreantts_settings";
const DEFAULT_VOICE = "default";
const DEFAULT_SETTINGS = { speed: 1.0, gapMs: 300, volume: 1.0, stopGapMs: DEFAULT_STOP_GAP_MS, voice: DEFAULT_VOICE };

function loadSettings() {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) return { ...DEFAULT_SETTINGS };
    return { ...DEFAULT_SETTINGS, ...JSON.parse(raw) };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

function saveSettings(settings) {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    // private browsing / storage disabled - settings just won't persist
  }
}

const settings = loadSettings();

const el = {
  text: document.getElementById("text"),
  btnPlay: document.getElementById("btnPlay"),
  btnStop: document.getElementById("btnStop"),
  btnPron: document.getElementById("btnPron"),
  btnSave: document.getElementById("btnSave"),
  pronOutput: document.getElementById("pronOutput"),
  status: document.getElementById("status"),
  voice: document.getElementById("voice"),
  speed: document.getElementById("speed"),
  speedValue: document.getElementById("speedValue"),
  gap: document.getElementById("gap"),
  gapValue: document.getElementById("gapValue"),
  volume: document.getElementById("volume"),
  volumeValue: document.getElementById("volumeValue"),
  stopGap: document.getElementById("stopGap"),
  stopGapValue: document.getElementById("stopGapValue"),
};

el.speed.value = settings.speed;
el.gap.value = settings.gapMs;
el.volume.value = Math.round(settings.volume * 100);
el.stopGap.value = settings.stopGapMs;
syncLabels();

function syncLabels() {
  el.speedValue.textContent = `${parseFloat(el.speed.value).toFixed(2)}x`;
  el.gapValue.textContent = `${el.gap.value}ms`;
  el.volumeValue.textContent = `${el.volume.value}%`;
  el.stopGapValue.textContent = `${el.stopGap.value}ms`;
}

for (const [input, key, transform] of [
  [el.speed, "speed", parseFloat],
  [el.gap, "gapMs", (v) => parseInt(v, 10)],
  [el.volume, "volume", (v) => parseInt(v, 10) / 100],
  [el.stopGap, "stopGapMs", (v) => parseInt(v, 10)],
]) {
  input.addEventListener("input", () => {
    settings[key] = transform(input.value);
    syncLabels();
    saveSettings(settings);
  });
}

// sound/ itself is always the "default" voice; sound/voices.json (written
// by sync-sound-assets.bat) lists any additional voice subfolders as
// {name, type} objects - see korean_tts.py's "Sound banks" section for the
// full picture (bank.json, other types like "full-syllable"). Only types
// this page actually knows how to build audio for are offered here - a
// bank of some other type still gets synced and listed in voices.json (so
// nothing needs re-running once this page's audio engine learns a new
// type), it just doesn't show up as selectable yet. Missing/unreachable
// manifest, or an old plain-string-array voices.json, just means "only
// default".
const KNOWN_BANK_TYPES = ["pieces"];

async function initVoices() {
  let voices = [DEFAULT_VOICE];
  try {
    const res = await fetch("sound/voices.json");
    if (res.ok) {
      const data = await res.json();
      if (Array.isArray(data.voices) && data.voices.length) {
        const names = data.voices
          .filter((v) => typeof v === "string" || KNOWN_BANK_TYPES.includes(v.type))
          .map((v) => (typeof v === "string" ? v : v.name));
        if (names.length) voices = names;
      }
    }
  } catch {
    // no manifest reachable - just the default voice
  }

  el.voice.innerHTML = "";
  for (const v of voices) {
    const opt = document.createElement("option");
    opt.value = v;
    opt.textContent = v;
    el.voice.appendChild(opt);
  }
  if (!voices.includes(settings.voice)) settings.voice = DEFAULT_VOICE;
  el.voice.value = settings.voice;
}
initVoices();

el.voice.addEventListener("change", () => {
  settings.voice = el.voice.value;
  saveSettings(settings);
});

// --- audio playback plumbing ---

let audioCtx = null;
function getAudioContext() {
  if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  return audioCtx;
}

let currentSource = null;

async function loadSampleBuffer(name) {
  try {
    const res = await fetch(`sound/${settings.voice}/${name}.wav`);
    if (!res.ok) return null;
    return await res.arrayBuffer();
  } catch {
    return null;
  }
}

function setStatus(msg) {
  el.status.textContent = msg;
  el.status.hidden = !msg;
}

function currentText() {
  return el.text.value.trim();
}

async function build() {
  const groups = textToGroups(currentText());
  return buildAudio(groups, loadSampleBuffer, {
    gapMs: settings.gapMs,
    stopGapMs: settings.stopGapMs,
    speed: settings.speed,
  });
}

function int16ToAudioBuffer(samples, sampleRate) {
  const ctx = getAudioContext();
  const buffer = ctx.createBuffer(1, Math.max(1, samples.length), sampleRate);
  const channel = buffer.getChannelData(0);
  for (let i = 0; i < samples.length; i++) channel[i] = samples[i] / 32768;
  return buffer;
}

function stopPlayback() {
  if (currentSource) {
    try {
      currentSource.stop();
    } catch {
      // already stopped
    }
    currentSource = null;
  }
}

async function playCurrent() {
  const text = currentText();
  if (!text) return;

  stopPlayback();
  setStatus("불러오는 중...");
  el.btnPlay.disabled = true;
  try {
    const { samples, missing } = await build();
    if (!samples.length) {
      setStatus("읽을 수 있는 한글이 없습니다.");
      return;
    }
    const ctx = getAudioContext();
    await ctx.resume(); // browsers suspend AudioContext until a user gesture; the click that got us here counts
    const buffer = int16ToAudioBuffer(samples, 44100);
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    const gain = ctx.createGain();
    gain.gain.value = settings.volume;
    source.connect(gain).connect(ctx.destination);
    source.onended = () => {
      if (currentSource === source) currentSource = null;
    };
    currentSource = source;
    source.start();

    if (missing.length) {
      setStatus(`음성 조각을 찾지 못해 건너뜀: ${[...new Set(missing)].join(", ")}`);
    } else {
      setStatus("");
    }
  } catch (e) {
    setStatus(`오류: ${e.message}`);
  } finally {
    el.btnPlay.disabled = false;
  }
}

async function saveCurrent() {
  const text = currentText();
  if (!text) return;

  setStatus("만드는 중...");
  el.btnSave.disabled = true;
  try {
    const { samples, missing } = await build();
    if (!samples.length) {
      setStatus("읽을 수 있는 한글이 없습니다.");
      return;
    }
    const wavBuffer = toWavBytes(samples);
    const blob = new Blob([wavBuffer], { type: "audio/wav" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "tts.wav";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    setStatus(missing.length ? `음성 조각을 찾지 못해 건너뜀: ${[...new Set(missing)].join(", ")}` : "저장됨: tts.wav");
  } catch (e) {
    setStatus(`오류: ${e.message}`);
  } finally {
    el.btnSave.disabled = false;
  }
}

function togglePronunciation() {
  const text = currentText();
  if (!text) return;
  if (el.pronOutput.hidden) {
    el.pronOutput.textContent = `발음: ${textToPronunciation(text)}`;
    el.pronOutput.hidden = false;
  } else {
    el.pronOutput.hidden = true;
  }
}

el.btnPlay.addEventListener("click", playCurrent);
el.btnStop.addEventListener("click", stopPlayback);
el.btnPron.addEventListener("click", togglePronunciation);
el.btnSave.addEventListener("click", saveCurrent);
