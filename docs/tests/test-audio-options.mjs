// Cross-checks non-default buildAudio() options (speed, normalize off,
// crossfade off, stop-gap variations, custom gap) against Python.
import { readFileSync, existsSync } from "node:fs";
import { buildAudio } from "../korean-audio.js";
import { textToGroups } from "../korean-phonology.js";

const ref = JSON.parse(readFileSync(new URL("./audio_options_reference.json", import.meta.url)));
const SOUND_DIR = new URL("../sound/default/", import.meta.url);

async function loadSampleBuffer(name) {
  const path = new URL(`${name}.wav`, SOUND_DIR);
  if (!existsSync(path)) return null;
  const buf = readFileSync(path);
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
}

function arraysEqual(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

const optionsFor = {
  "speed_1.5": { speed: 1.5 },
  "speed_0.75": { speed: 0.75 },
  no_normalize: { normalize: false },
  no_crossfade: { crossfade: false },
  stop_gap_0: { stopGapMs: 0 },
  stop_gap_100: { stopGapMs: 100 },
  gap_500: { gapMs: 500 },
};

const groups = textToGroups(ref.phrase);
let failures = 0;
for (const [key, opts] of Object.entries(optionsFor)) {
  const expected = ref.configs[key];
  // sound/default is hex_pieces (see korean_tts.py's piece_filename) -
  // buildAudio() translates internally, so loadSampleBuffer above stays
  // untranslated.
  const { samples, missing } = await buildAudio(groups, loadSampleBuffer, { hexPieces: true, ...opts });
  const got = Array.from(samples);
  const ok = arraysEqual(got, expected.samples) && JSON.stringify(missing) === JSON.stringify(expected.missing);
  if (!ok) {
    failures++;
    console.log(`FAIL ${key}: length got=${got.length} want=${expected.samples.length}`);
  } else {
    console.log(`OK   ${key} (${got.length} samples, exact match)`);
  }
}
console.log(failures === 0 ? "\nALL PASS" : `\n${failures} TOTAL FAILURES`);
process.exit(failures === 0 ? 0 : 1);
