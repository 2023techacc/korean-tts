// Cross-checks korean-audio.js against audio_reference.json, an exact PCM
// dump from the already-validated Python engine (korean_tts.py), using the
// REAL sound/*.wav files (loaded via fs, not synthetic data). Run with:
//   node test-audio.mjs
import { readFileSync, existsSync } from "node:fs";
import { readSample, buildAudio, pieceFilename } from "../korean-audio.js";
import { textToGroups } from "../korean-phonology.js";

const ref = JSON.parse(readFileSync(new URL("./audio_reference.json", import.meta.url)));
const SOUND_DIR = new URL("../sound/default/", import.meta.url);

// sound/default is hex_pieces (see korean_tts.py's piece_filename) - this
// loads by the ACTUAL on-disk filename. buildAudio() does its own
// translation internally (see hexPieces below), so loadSampleBuffer stays
// untranslated to avoid double-applying it; the standalone sample-loading
// test below translates explicitly since it calls this directly with a
// logical name from audio_reference.json.
function loadFileBuffer(fileName) {
  const path = new URL(`${fileName}.wav`, SOUND_DIR);
  if (!existsSync(path)) return null;
  const buf = readFileSync(path);
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
}

async function loadSampleBuffer(fileName) {
  return loadFileBuffer(fileName);
}

function arraysEqual(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

let failures = 0;

console.log("--- individual sample loading (parse+downmix+resample+trim+normalize) ---");
for (const [name, expected] of Object.entries(ref.samples)) {
  const buf = loadFileBuffer(pieceFilename(name, true));
  const got = Array.from(readSample(buf));
  const ok = arraysEqual(got, expected);
  if (!ok) {
    failures++;
    console.log(`FAIL ${name}: length got=${got.length} want=${expected.length}`);
    for (let i = 0; i < Math.min(got.length, expected.length); i++) {
      if (got[i] !== expected[i]) {
        console.log(`  first diff at index ${i}: got=${got[i]} want=${expected[i]}`);
        break;
      }
    }
  } else {
    console.log(`OK   ${name} (${got.length} samples, exact match)`);
  }
}

console.log("\n--- full build_audio (default settings) ---");
for (const [phrase, expected] of Object.entries(ref.builds)) {
  const groups = textToGroups(phrase);
  const { samples, missing } = await buildAudio(groups, loadSampleBuffer, { hexPieces: true });
  const got = Array.from(samples);
  const ok = arraysEqual(got, expected.samples) && JSON.stringify(missing) === JSON.stringify(expected.missing);
  if (!ok) {
    failures++;
    console.log(`FAIL ${JSON.stringify(phrase)}: length got=${got.length} want=${expected.samples.length}`);
    for (let i = 0; i < Math.min(got.length, expected.samples.length); i++) {
      if (got[i] !== expected.samples[i]) {
        console.log(`  first diff at index ${i}: got=${got[i]} want=${expected.samples[i]}`);
        break;
      }
    }
  } else {
    console.log(`OK   ${JSON.stringify(phrase)} (${got.length} samples, exact match, missing=${JSON.stringify(missing)})`);
  }
}

console.log(failures === 0 ? "\nALL PASS" : `\n${failures} TOTAL FAILURES`);
process.exit(failures === 0 ? 0 : 1);
