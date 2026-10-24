"use node";

import { v } from "convex/values";
import { action } from "./_generated/server";
import { api } from "./_generated/api";
import { synthesizeSpeech, type SpeechResult } from "./lib/cartesia";

/**
 * Public authenticated text-to-speech for conversational output (priority 4:
 * menu reading, confirmations, results). Returns WAV for R1-controlled playback.
 * Not for Stop, hazards or hand-guidance cues: those stay on-device.
 * On ok === false the caller speaks the same text with the on-device voice.
 */
export const synthesize = action({
  args: { requestId: v.string(), text: v.string() },
  handler: async (ctx, args): Promise<SpeechResult> => {
    await ctx.runQuery(api.users.me, {});
    return synthesizeSpeech(args);
  },
});
