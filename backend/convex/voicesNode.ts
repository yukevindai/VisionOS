"use node";

import { ConvexError, v } from "convex/values";
import { action } from "./_generated/server";
import { internal } from "./_generated/api";
import {
  MAX_CLONE_BYTES, MAX_CLONE_SECONDS, MIN_CLONE_SECONDS, cloneVoice, deleteVoice, wavSeconds,
} from "./lib/cartesia";
import { consentSentence } from "./voices";

// Family Voice actions (see voices.ts for the consent rules). Cartesia calls live here.

function bad(code: string): never {
  throw new ConvexError({ code });
}

/**
 * Turns the helper's consented recording into their own voice (Cartesia Instant Voice Clone).
 * The recording must be a 16-bit PCM WAV of 10–60 s recorded in the page; it is deleted from
 * storage whatever happens, so only the voice ID remains.
 */
export const create = action({
  args: { storageId: v.id("_storage"), consent: v.literal(true) },
  handler: async (ctx, { storageId }): Promise<{ ok: true; seconds: number }> => {
    try {
      const me = await ctx.runQuery(internal.voices.me, {});
      if (me.voiceId) bad("VOICE_EXISTS");
      const blob = await ctx.storage.get(storageId);
      if (!blob || blob.size > MAX_CLONE_BYTES) bad("INVALID_RECORDING");
      const clip = new Uint8Array(await blob.arrayBuffer());
      const seconds = wavSeconds(clip);
      if (seconds === null) bad("INVALID_RECORDING");
      if (seconds < MIN_CLONE_SECONDS) bad("RECORDING_TOO_SHORT");
      if (seconds > MAX_CLONE_SECONDS + 1) bad("RECORDING_TOO_LONG");
      const consentText = consentSentence(me.displayName);
      const result = await cloneVoice(clip, `VisionOS family: ${me.displayName || "helper"}`,
        `Recorded with spoken consent in VisionOS Family Notes ("${consentText}"). ` +
        "Used only to read this person's own notes to their family.");
      if (!result.ok) {
        throw new ConvexError({ code: "VOICE_CLONE_FAILED", reason: result.errorCode,
          ...(result.httpStatus !== undefined ? { httpStatus: result.httpStatus } : {}) });
      }
      const saved = await ctx.runMutation(internal.voices.save, { userId: me.userId, voiceId: result.voiceId,
        consentText, recordingSeconds: Math.round(seconds * 10) / 10 });
      if (!saved) { // a second tab won the race: do not keep an unused copy of their voice
        await deleteVoice(result.voiceId);
        bad("VOICE_EXISTS");
      }
      return { ok: true, seconds: Math.round(seconds * 10) / 10 };
    } finally {
      await ctx.runMutation(internal.voices.discardUpload, { storageId });
    }
  },
});

/** Withdraws consent: deletes the voice at Cartesia first, then everywhere it was used. */
export const remove = action({
  args: {},
  handler: async (ctx): Promise<{ removed: boolean; rerendered: number }> => {
    const me = await ctx.runQuery(internal.voices.me, {});
    if (!me.voiceId) return { removed: false, rerendered: 0 };
    const deleted = await deleteVoice(me.voiceId);
    // Keep the record if Cartesia still has the voice, so the helper can try again.
    if (!deleted.ok) bad("VOICE_DELETE_FAILED");
    const rerendered: number = await ctx.runMutation(internal.voices.forget, { userId: me.userId });
    return { removed: true, rerendered };
  },
});
