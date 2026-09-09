// Loads the bundled sound/*.wav samples and assembles them into one PCM
// track per korean-phonology.js's textToGroups() result: loudness-matched,
// silence-trimmed, crossfaded within each syllable group, with a bare
// sonorant-coda recording (ㄴㄹㅁㅇ) shortened to a quick closure first,
// and extra silence after an obstruent-stop batchim (ㄱ/ㄷ/ㅂ).
//
// Direct port of korean_tts.py's audio pipeline - same constants, same
// order of operations. See that file for the measurements behind the
// default numbers.
//
// Sample loading is injected as `loadSampleBuffer(name) -> Promise<ArrayBuffer|null>`
// rather than hardcoding fetch(), so the exact same code runs in the
// browser (fetch) and under Node for testing (fs.readFileSync) - see
// test-audio.mjs, which cross-checks this against korean_tts.py's own
// build_audio() output for the real sound/ files.

import { PAUSE } from "./korean-phonology.js";

export const TARGET_RATE = 44100;

// The 268 bundled samples span a 45x RMS range and one file already peaks
// near full scale, so quiet syllables are close to inaudible next to loud
// ones. Same values as korean_tts.py (a "+4dB louder baseline" bump from
// the original 2200/6.0/30000; only the 3% quietest outliers fall notably
// short of target, and even those still land at 40%+ rather than silent).
const NORMALIZE_TARGET_RMS = 3500;
const NORMALIZE_MAX_GAIN = 8.0;
const NORMALIZE_PEAK_LIMIT = 32000;
const SILENCE_THRESHOLD = 250;

export const MIN_SPEED = 0.5;
export const MAX_SPEED = 2.0;

// How much of the shorter of two adjacent clips to overlap, as a fraction
// of its length: generous for a diphthong (blending vowel qualities
// together is correct), lighter for a sonorant coda (don't swallow the
// consonant's identity).
const CROSSFADE_FRACTION = { diphthong: 0.5, coda: 0.25 };
const CROSSFADE_MIN_MS = 20;
const CROSSFADE_MAX_MS = 150;

// The bare consonant recordings standing in for a sonorant batchim run
// 150-230ms on their own (recorded as a full mini-syllable, not a quick
// closure) - truncated before crossfading kicks in. Unambiguous: an
// onset+vowel sample is always 2+ romanised letters glued together (e.g.
// "ba", "go"), so a bare "n"/"l"/"m"/"ng" only ever occurs in coda position.
const CODA_TAILS = new Set(["n", "l", "m", "ng"]);
const CODA_MAX_MS = 120;
const CODA_TAIL_FADE_MS = 20;

// Closed syllables ending in an unreleased obstruent stop (representative
// ㄱ/ㄷ/ㅂ - covers ㅋ/ㄲ, ㅅ/ㅆ/ㅈ/ㅊ/ㅌ/ㅎ, ㅍ too) run straight into the
// next syllable with zero gap otherwise. Detectable from the sample name
// alone: such a syllable's last sample always ends in exactly g/d/b (e.g.
// "ag", "ug", "ad") - the sole exception, the sonorant tail "ng", is
// excluded via CODA_TAILS.
export const DEFAULT_STOP_GAP_MS = 40;

function endsInStopCoda(name) {
  return Boolean(name) && !CODA_TAILS.has(name) && "gdb".includes(name[name.length - 1]);
}

// Every romanized base piece name -> the hex codepoint of a real character
// that produces exactly that recording (e.g. ga -> "ac00" == 가), used only
// when a bank's bank.json sets settings.hex_pieces (see pieceFilename
// below). Generated from korean_tts.py's PIECE_REPRESENTATIVE_CHARS - a
// fixed, verified table, not re-derived here, so the ports can't drift
// apart. "n"/"l"/"m"/"ng" get a "_tail" suffix: those four are recorded
// SEPARATELY from the ordinary onset+vowel piece that happens to share the
// same representative character (e.g. "n"'s 느 is also "neu"'s 느, but as a
// trimmed coda-closure take, not the same file) - without the suffix,
// hex-naming would silently collapse them onto one file.
export const PIECE_HEX_NAMES = {
  a: "c544", ab: "c555", ad: "c54b", ae: "c560", aeb: "c571",
  aed: "c567", aeg: "c561", ag: "c545", ba: "bc14", bae: "bc30",
  bba: "be60", bbae: "be7c", bbeo: "bed0", bbeu: "c058", bbi: "c090",
  bbo: "bf40", bbu: "bfcc", bbya: "be98", bbyae: "beb4", bbyeo: "bf08",
  bbyo: "bfb0", bbyu: "c03c", beo: "bc84", beu: "be0c", bi: "be44",
  bo: "bcf4", bu: "bd80", bya: "bc4c", byae: "bc68", byeo: "bcbc",
  byo: "bd64", byu: "bdf0", cha: "cc28", chae: "cc44", cheo: "cc98",
  cheu: "ce20", chi: "ce58", cho: "cd08", chu: "cd94", da: "b2e4",
  dae: "b300", dda: "b530", ddae: "b54c", ddeo: "b5a0", ddeu: "b728",
  ddi: "b760", ddo: "b610", ddu: "b69c", ddya: "b568", ddyae: "b584",
  ddyeo: "b5d8", ddyo: "b680", ddyu: "b70c", deo: "b354", deu: "b4dc",
  di: "b514", do: "b3c4", du: "b450", dya: "b31c", dyae: "b338",
  dyeo: "b38c", dyo: "b434", dyu: "b4c0", eo: "c5b4", eob: "c5c5",
  eod: "c5bb", eog: "c5b5", eu: "c73c", eub: "c74d", eud: "c743",
  eug: "c73d", ga: "ac00", gae: "ac1c", geo: "ac70", geu: "adf8",
  gga: "ae4c", ggae: "ae68", ggeo: "aebc", ggeu: "b044", ggi: "b07c",
  ggo: "af2c", ggu: "afb8", ggya: "ae84", ggyae: "aea0", ggyeo: "aef4",
  ggyo: "af9c", ggyu: "b028", gi: "ae30", go: "ace0", gu: "ad6c",
  gya: "ac38", gyae: "ac54", gyeo: "aca8", gyo: "ad50", gyu: "addc",
  ha: "d558", hae: "d574", heo: "d5c8", heu: "d750", hi: "d788",
  ho: "d638", hu: "d6c4", hya: "d590", hyae: "d5ac", hyeo: "d600",
  hyo: "d6a8", hyu: "d734", i: "c774", ib: "c785", id: "c77b",
  ig: "c775", ja: "c790", jae: "c7ac", jeo: "c800", jeu: "c988",
  ji: "c9c0", jja: "c9dc", jjae: "c9f8", jjeo: "ca4c", jjeu: "cbd4",
  jji: "cc0c", jjo: "cabc", jju: "cb48", jo: "c870", ju: "c8fc",
  ka: "ce74", kae: "ce90", keo: "cee4", keu: "d06c", ki: "d0a4",
  ko: "cf54", ku: "cfe0", kya: "ceac", kyae: "cec8", kyeo: "cf1c",
  kyo: "cfc4", kyu: "d050", l: "b974_tail", la: "b77c", lae: "b798",
  leo: "b7ec", leu: "b974", li: "b9ac", lo: "b85c", lu: "b8e8",
  lya: "b7b4", lyae: "b7d0", lyeo: "b824", lyo: "b8cc", lyu: "b958",
  m: "bbc0_tail", ma: "b9c8", mae: "b9e4", meo: "ba38", meu: "bbc0",
  mi: "bbf8", mo: "baa8", mu: "bb34", mya: "ba00", myae: "ba1c",
  myeo: "ba70", myo: "bb18", myu: "bba4", n: "b290_tail", na: "b098",
  nae: "b0b4", neo: "b108", neu: "b290", ng: "c751_tail", ni: "b2c8",
  no: "b178", nu: "b204", nya: "b0d0", nyae: "b0ec", nyeo: "b140",
  nyo: "b1e8", nyu: "b274", o: "c624", ob: "c635", od: "c62b",
  og: "c625", pa: "d30c", pae: "d328", peo: "d37c", peu: "d504",
  pi: "d53c", po: "d3ec", pu: "d478", pya: "d344", pyae: "d360",
  pyeo: "d3b4", pyo: "d45c", pyu: "d4e8", sa: "c0ac", sae: "c0c8",
  seo: "c11c", seu: "c2a4", si: "c2dc", so: "c18c", ssa: "c2f8",
  ssae: "c314", sseo: "c368", sseu: "c4f0", ssi: "c528", sso: "c3d8",
  ssu: "c464", ssya: "c330", ssyae: "c34c", ssyeo: "c3a0", ssyo: "c448",
  ssyu: "c4d4", su: "c218", sya: "c0e4", syae: "c100", syeo: "c154",
  syo: "c1fc", syu: "c288", ta: "d0c0", tae: "d0dc", teo: "d130",
  teu: "d2b8", ti: "d2f0", to: "d1a0", tu: "d22c", tya: "d0f8",
  tyae: "d114", tyeo: "d168", tyo: "d210", tyu: "d29c", u: "c6b0",
  ub: "c6c1", ud: "c6b7", ug: "c6b1", ya: "c57c", yab: "c58d",
  yad: "c583", yae: "c598", yaeb: "c5a9", yaed: "c59f", yaeg: "c599",
  yag: "c57d", yeo: "c5ec", yeob: "c5fd", yeod: "c5f3", yeog: "c5ed",
  yo: "c694", yob: "c6a5", yod: "c69b", yog: "c695", yu: "c720",
  yub: "c731", yud: "c727", yug: "c721",
};

export function pieceFilename(name, hexPieces) {
  if (!hexPieces) return name;
  return PIECE_HEX_NAMES[name] ?? name;
}

class AudioError extends Error {}

// ------------------------------------------------------
// WAV parsing
// ------------------------------------------------------

// Minimal RIFF/WAVE chunk scanner: robust to extra chunks before "data",
// unlike assuming a fixed 44-byte header.
function parseWav(arrayBuffer) {
  const view = new DataView(arrayBuffer);
  const bytes = new Uint8Array(arrayBuffer);
  const tag = (off) => String.fromCharCode(bytes[off], bytes[off + 1], bytes[off + 2], bytes[off + 3]);

  if (arrayBuffer.byteLength < 12 || tag(0) !== "RIFF" || tag(8) !== "WAVE") {
    throw new AudioError("not a RIFF/WAVE file");
  }

  let pos = 12;
  let channels = -1;
  let sampleRate = -1;
  let bitsPerSample = -1;
  let pcmOffset = -1;
  let pcmLength = 0;

  while (pos + 8 <= arrayBuffer.byteLength) {
    const chunkId = tag(pos);
    const chunkSize = view.getUint32(pos + 4, true);
    const body = pos + 8;
    if (chunkId === "fmt ") {
      channels = view.getUint16(body + 2, true);
      sampleRate = view.getUint32(body + 4, true);
      bitsPerSample = view.getUint16(body + 14, true);
    } else if (chunkId === "data") {
      pcmOffset = body;
      pcmLength = Math.min(chunkSize, arrayBuffer.byteLength - body);
    }
    pos = body + chunkSize + (chunkSize & 1); // chunks are word-aligned; skip the pad byte
  }

  if (channels < 0 || pcmOffset < 0) throw new AudioError("malformed WAV (missing fmt/data chunk)");
  return { channels, sampleRate, bitsPerSample, pcmOffset, pcmLength };
}

function rms(samples) {
  if (!samples.length) return 0;
  let sumSq = 0;
  for (let i = 0; i < samples.length; i++) sumSq += samples[i] * samples[i];
  return Math.sqrt(sumSq / samples.length);
}

function trimSilence(samples, threshold = SILENCE_THRESHOLD) {
  let start = 0;
  while (start < samples.length && Math.abs(samples[start]) < threshold) start++;
  let end = samples.length;
  while (end > start && Math.abs(samples[end - 1]) < threshold) end--;
  return samples.subarray(start, end);
}

function normalizeLoudness(samples, targetRms = NORMALIZE_TARGET_RMS, maxGain = NORMALIZE_MAX_GAIN, peakLimit = NORMALIZE_PEAK_LIMIT) {
  if (!samples.length) return samples;
  const r = rms(samples);
  if (r <= 0) return samples;

  let gain = Math.min(targetRms / r, maxGain);
  let peak = 0;
  for (let i = 0; i < samples.length; i++) peak = Math.max(peak, Math.abs(samples[i]));
  if (peak > 0 && peak * gain > peakLimit) gain = peakLimit / peak;

  if (Math.abs(gain - 1.0) < 0.01) return samples;
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    out[i] = Math.max(-32768, Math.min(32767, Math.trunc(samples[i] * gain)));
  }
  return out;
}

// Nearest-neighbour resample to TARGET_RATE. All bundled samples are
// already 44100Hz, so this mostly guards against an odd file being dropped in.
function resample(samples, srcRate) {
  if (srcRate === TARGET_RATE || !samples.length) return samples;
  const n = Math.max(1, Math.floor((samples.length * TARGET_RATE) / srcRate));
  const last = samples.length - 1;
  const out = new Int16Array(n);
  for (let i = 0; i < n; i++) {
    out[i] = samples[Math.min(last, Math.floor((i * srcRate) / TARGET_RATE))];
  }
  return out;
}

// Speed up or slow down playback by `speed`x, by resampling: the same
// trick as resample() above, just read backwards (pretend the source rate
// was TARGET_RATE*speed, then resample back down). Pitch shifts with
// speed, matching tts.py's --speed (a real time-stretch needs a phase
// vocoder / WSOLA, out of scope here - the same trade-off as the desktop
// CLI, for the same reason: no extra dependency).
export function changeSpeed(samples, speed) {
  const clamped = Math.max(MIN_SPEED, Math.min(MAX_SPEED, speed));
  return resample(samples, Math.max(1, Math.round(TARGET_RATE * clamped)));
}

// Load one sample from a WAV ArrayBuffer as mono 16-bit @ TARGET_RATE,
// trimmed and loudness-matched.
export function readSample(arrayBuffer, normalize = true) {
  const wav = parseWav(arrayBuffer);
  if (wav.bitsPerSample !== 16) {
    throw new AudioError(`expected 16-bit audio, got ${wav.bitsPerSample}-bit`);
  }

  // WAV PCM is little-endian; DataView with littleEndian=true handles that directly.
  const view = new DataView(arrayBuffer, wav.pcmOffset, wav.pcmLength);
  const frameCount = Math.floor(wav.pcmLength / 2);
  let samples = new Int16Array(frameCount);
  for (let i = 0; i < frameCount; i++) samples[i] = view.getInt16(i * 2, true);

  // The bundled sound/ folder mixes mono and stereo files; downmix to mono.
  // Uses Math.floor, not Math.trunc: Python's `sum(...) // channels` is a
  // floor division, which differs from trunc-toward-zero for negative
  // sums (-5 // 2 == -3, but trunc(-5/2) == -2) - audio oscillates
  // negative constantly, so this isn't a rare edge case.
  if (wav.channels > 1) {
    const ch = wav.channels;
    const usable = samples.length - (samples.length % ch);
    const mono = new Int16Array(Math.floor(usable / ch));
    for (let i = 0; i < mono.length; i++) {
      let sum = 0;
      for (let c = 0; c < ch; c++) sum += samples[i * ch + c];
      mono[i] = Math.floor(sum / ch);
    }
    samples = mono;
  }

  samples = resample(samples, wav.sampleRate);
  samples = trimSilence(samples);
  if (normalize) samples = normalizeLoudness(samples);
  return samples;
}

// ------------------------------------------------------
// Assembly: fades, coda shortening, crossfade
// ------------------------------------------------------

function applyFade(samples, fadeLen) {
  const len = Math.min(fadeLen, Math.floor(samples.length / 2));
  for (let i = 0; i < len; i++) {
    const factor = i / len;
    samples[i] = Math.trunc(samples[i] * factor);
    samples[samples.length - 1 - i] = Math.trunc(samples[samples.length - 1 - i] * factor);
  }
}

function shortenCoda(samples, maxMs = CODA_MAX_MS, fadeMs = CODA_TAIL_FADE_MS) {
  const maxLen = Math.floor((TARGET_RATE * maxMs) / 1000);
  if (samples.length <= maxLen) return samples;
  const cut = samples.slice(0, maxLen);
  const fadeLen = Math.min(Math.floor((TARGET_RATE * fadeMs) / 1000), Math.floor(cut.length / 2));
  for (let i = 0; i < fadeLen; i++) {
    const factor = i / fadeLen;
    const idx = cut.length - 1 - i;
    cut[idx] = Math.trunc(cut[idx] * factor);
  }
  return cut;
}

function overlapLen(kind, lenA, lenB) {
  const fraction = CROSSFADE_FRACTION[kind] ?? 0;
  if (fraction <= 0 || !lenA || !lenB) return 0;
  const shorter = Math.min(lenA, lenB);
  const lo = Math.floor((TARGET_RATE * CROSSFADE_MIN_MS) / 1000);
  const hi = Math.floor((TARGET_RATE * CROSSFADE_MAX_MS) / 1000);
  let ov = Math.trunc(shorter * fraction);
  ov = Math.max(lo, ov);
  ov = Math.min(ov, hi);
  ov = Math.min(ov, shorter - 1);
  return Math.max(0, ov);
}

// Equal-power overlap-add join: shorter than concatenation, and the seam sounds continuous.
function crossfadeJoin(chunks, kind) {
  let result = chunks[0];
  for (let i = 1; i < chunks.length; i++) {
    const nxt = chunks[i];
    let ov = overlapLen(kind, chunks[i - 1].length, nxt.length);
    ov = Math.min(ov, result.length, nxt.length);
    if (ov <= 0) {
      const merged = new Int16Array(result.length + nxt.length);
      merged.set(result, 0);
      merged.set(nxt, result.length);
      result = merged;
      continue;
    }
    const merged = new Int16Array(result.length + nxt.length - ov);
    merged.set(result, 0);
    const base = result.length - ov;
    for (let k = 0; k < ov; k++) {
      const t = (k + 1) / (ov + 1);
      const fadeOut = Math.cos((t * Math.PI) / 2);
      const fadeIn = Math.sin((t * Math.PI) / 2);
      const mixed = result[base + k] * fadeOut + nxt[k] * fadeIn;
      merged[base + k] = Math.max(-32768, Math.min(32767, Math.trunc(mixed)));
    }
    merged.set(nxt.subarray(ov), base + ov);
    result = merged;
  }
  return result;
}

// ------------------------------------------------------
// Public entry point
// ------------------------------------------------------

// Concatenate grouped samples (from textToGroups) into one mono 16-bit
// @44100Hz PCM track. `loadSampleBuffer(name)` must return a
// Promise<ArrayBuffer|null> for "sound/<name>.wav" (null if missing).
export async function buildAudio(groups, loadSampleBuffer, options = {}) {
  const {
    gapMs = 300,
    fadeMs = 5,
    normalize = true,
    crossfade = true,
    speed = 1.0,
    stopGapMs = DEFAULT_STOP_GAP_MS,
    hexPieces = false,
  } = options;

  const chunks = [];
  const gap = new Int16Array(Math.floor((TARGET_RATE * gapMs) / 1000));
  const stopGap = new Int16Array(Math.floor((TARGET_RATE * stopGapMs) / 1000));
  const fadeLen = Math.floor((TARGET_RATE * fadeMs) / 1000);
  const missing = [];
  const rawCache = new Map();
  let prevEndsInStop = false;
  let totalLength = 0;

  // `name` is always the stable LOGICAL romanized name (e.g. "ga") text_to_
  // groups-equivalent output constructs - `fileName` is what's actually
  // fetched/cached/reported missing (see pieceFilename). Already-hex names
  // pass through unchanged, so this is safe regardless of hexPieces.
  async function loadRaw(fileName) {
    if (rawCache.has(fileName)) return rawCache.get(fileName);
    let raw = null;
    const buf = await loadSampleBuffer(fileName);
    if (buf) raw = readSample(buf, normalize);
    rawCache.set(fileName, raw);
    return raw;
  }

  for (const group of groups) {
    if (group.names.length === 1 && group.names[0] === PAUSE) {
      chunks.push(gap);
      totalLength += gap.length;
      prevEndsInStop = false;
      continue;
    }

    if (prevEndsInStop && stopGapMs > 0) {
      chunks.push(stopGap);
      totalLength += stopGap.length;
    }

    const groupChunks = [];
    for (let i = 0; i < group.names.length; i++) {
      const name = group.names[i];
      const fileName = pieceFilename(name, hexPieces);
      const raw = await loadRaw(fileName);
      if (!raw) {
        missing.push(fileName);
        continue;
      }
      let chunk = raw.slice(); // copy: about to be mutated
      if (i > 0 && CODA_TAILS.has(name)) chunk = shortenCoda(chunk); // a batchim, not this syllable's onset
      groupChunks.push(chunk);
    }
    if (groupChunks.length === 0) continue;

    let combined;
    if (crossfade && groupChunks.length > 1) {
      combined = crossfadeJoin(groupChunks, group.kind);
    } else {
      combined = groupChunks[0];
      if (groupChunks.length > 1) {
        for (let i = 1; i < groupChunks.length; i++) {
          const merged = new Int16Array(combined.length + groupChunks[i].length);
          merged.set(combined, 0);
          merged.set(groupChunks[i], combined.length);
          combined = merged;
        }
      }
    }

    if (fadeLen > 0) applyFade(combined, fadeLen);
    chunks.push(combined);
    totalLength += combined.length;
    prevEndsInStop = endsInStopCoda(group.names[group.names.length - 1]);
  }

  let track = new Int16Array(totalLength);
  let offset = 0;
  for (const chunk of chunks) {
    track.set(chunk, offset);
    offset += chunk.length;
  }

  if (speed !== 1.0) track = changeSpeed(track, speed);
  return { samples: track, missing };
}

// ------------------------------------------------------
// WAV export (for download / save)
// ------------------------------------------------------

export function toWavBytes(samples) {
  const dataSize = samples.length * 2;
  const buf = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buf);
  const writeTag = (offset, str) => {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
  };
  writeTag(0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeTag(8, "WAVE");
  writeTag(12, "fmt ");
  view.setUint32(16, 16, true); // PCM fmt chunk size
  view.setUint16(20, 1, true); // AudioFormat = PCM
  view.setUint16(22, 1, true); // channels = mono
  view.setUint32(24, TARGET_RATE, true);
  view.setUint32(28, TARGET_RATE * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  writeTag(36, "data");
  view.setUint32(40, dataSize, true);
  for (let i = 0; i < samples.length; i++) view.setInt16(44 + i * 2, samples[i], true);
  return buf;
}

export function durationSeconds(samples) {
  return samples.length / TARGET_RATE;
}
