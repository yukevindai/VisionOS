// Manual check: one real Cartesia request (or SIMULATED without a key).
// Run from backend/:  node --env-file=.env scripts/cartesiaSmoke.ts "Your sentence"
// Costs about 1 credit per character. Writes backend/eval/results/cartesia-smoke.wav.
import { writeFileSync, mkdirSync } from "node:fs";
import { synthesizeSpeech } from "../convex/lib/cartesia.ts";

const text = process.argv[2] ?? "There are three options: Burgers, Drinks, and Desserts.";
const result = await synthesizeSpeech({ requestId: `smoke-${Date.now()}`, text });
if (!result.ok) {
  console.log(JSON.stringify(result, null, 2));
  process.exit(1);
}
mkdirSync("eval/results", { recursive: true });
writeFileSync("eval/results/cartesia-smoke.wav", Buffer.from(result.audioBase64, "base64"));
console.log(`${result.mode}: ${result.characters} chars, ${result.latencyMs} ms -> eval/results/cartesia-smoke.wav`);
