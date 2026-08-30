// Cross-checks korean-phonology.js against reference.json, a dump of the
// already-validated Python engine's output (korean_tts.py). Run with:
//   node test-phonology.mjs
import { readFileSync } from "node:fs";
import { textToPronunciation, textToGroups, textToSamples } from "../korean-phonology.js";

const ref = JSON.parse(readFileSync(new URL("./reference.json", import.meta.url)));

let failures = 0;

// --- exhaustive: every one of the 11,172 possible Hangul syllables ---
let checked = 0;
for (const [ch, expectedSamples] of Object.entries(ref.single)) {
  checked++;
  const got = textToSamples(ch);
  const ok = JSON.stringify(got) === JSON.stringify(expectedSamples);
  if (!ok) {
    failures++;
    if (failures <= 10) {
      console.log(`FAIL single ${JSON.stringify(ch)}: got ${JSON.stringify(got)} want ${JSON.stringify(expectedSamples)}`);
    }
  }
}
console.log(`single-syllable exhaustive: ${checked - failures}/${checked} match`);

// --- multi-syllable phrases: pronunciation + groups ---
let phraseFailures = 0;
for (const [phrase, expected] of Object.entries(ref.phrases)) {
  const gotPron = textToPronunciation(phrase);
  const gotGroups = textToGroups(phrase).map((g) => [g.kind, g.names]);
  const wantGroups = expected.groups;

  const pronOk = gotPron === expected.pron;
  const groupsOk = JSON.stringify(gotGroups) === JSON.stringify(wantGroups);

  if (!pronOk || !groupsOk) {
    phraseFailures++;
    console.log(`FAIL phrase ${JSON.stringify(phrase)}`);
    if (!pronOk) console.log(`  pron: got ${JSON.stringify(gotPron)} want ${JSON.stringify(expected.pron)}`);
    if (!groupsOk) console.log(`  groups: got ${JSON.stringify(gotGroups)} want ${JSON.stringify(wantGroups)}`);
  }
}
console.log(`phrases: ${Object.keys(ref.phrases).length - phraseFailures}/${Object.keys(ref.phrases).length} match`);

const total = failures + phraseFailures;
console.log(total === 0 ? "\nALL PASS" : `\n${total} TOTAL FAILURES`);
process.exit(total === 0 ? 0 : 1);
