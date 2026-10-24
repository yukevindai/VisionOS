"use node";

import { createHash } from "node:crypto";
import { Buffer } from "node:buffer";
import { v } from "convex/values";
import { action } from "./_generated/server";
import { api, internal } from "./_generated/api";
import { readCartesiaConfig, synthesizeSpeech, validateSpeechRequest, type SpeechResult } from "./lib/cartesia";

export type CachedSpeechResult = SpeechResult & { cached: boolean };

/** Cache key covers everything that changes the audio, so a new voice or model never reuses old audio. */
export function speechCacheKey(model: string, voiceId: string, text: string): string {
  return createHash("sha256").update(`${model}\n${voiceId}\n${text}`).digest("hex");
}

/**
 * Public authenticated text-to-speech for conversational output (priority 4:
 * menu reading, confirmations, results). Returns WAV for R1-controlled playback.
 * Not for Stop, hazards or hand-guidance cues: those stay on-device.
 * On ok === false the caller speaks the same text with the on-device voice.
 * Repeated sentences are served from the cache and spend no Cartesia credits.
 */
export const synthesize = action({
  args: { requestId: v.string(), text: v.string() },
  handler: async (ctx, args): Promise<CachedSpeechResult> => {
    await ctx.runQuery(api.users.me, {});
    const text = validateSpeechRequest(args);
    const config = readCartesiaConfig(process.env);
    if (config === null || config === "INCOMPLETE") {
      return { ...(await synthesizeSpeech(args)), cached: false };
    }

    const started = Date.now();
    const key = speechCacheKey(config.model, config.voiceId, text);
    const storageId = await ctx.runQuery(internal.speechCache.get, { key });
    if (storageId) {
      const blob = await ctx.storage.get(storageId);
      if (blob) {
        const audio = Buffer.from(await blob.arrayBuffer());
        return { ok: true, requestId: args.requestId, mode: "REAL_API", format: "wav", sampleRate: 24_000,
          audioBase64: audio.toString("base64"), characters: text.length, latencyMs: Date.now() - started, cached: true };
      }
      await ctx.runMutation(internal.speechCache.remove, { key }); // blob vanished: forget the stale row
    }

    const result = await synthesizeSpeech(args);
    if (result.ok && result.mode === "REAL_API") {
      const stored = await ctx.storage.store(new Blob([Buffer.from(result.audioBase64, "base64")], { type: "audio/wav" }));
      await ctx.runMutation(internal.speechCache.put, { key, storageId: stored, characters: result.characters });
    }
    return { ...result, cached: false };
  },
});
