"use node";

import { Buffer } from "node:buffer";
import { v } from "convex/values";
import { internalAction } from "./_generated/server";
import { internal } from "./_generated/api";
import { readCartesiaConfig, synthesizeSpeech } from "./lib/cartesia";

// Renders a family note to WAV once, right after it is saved, so the owner's device can
// play it instantly instead of waiting for Cartesia. Runs under the action-retrier
// component (see notes:create): throwing asks for a retry; returning ends the run.
// The note text is always available, so a note without audio is read by the device voice.

/** Worth retrying: timeouts, network errors, rate limits and provider 5xx. Not bad keys or quota. */
export function isRetryable(errorCode: string, httpStatus?: number): boolean {
  if (errorCode === "PROVIDER_TIMEOUT") return true;
  if (errorCode !== "PROVIDER_ERROR") return false;
  return httpStatus === undefined || httpStatus === 429 || httpStatus >= 500;
}

export const render = internalAction({
  args: { noteId: v.id("helperNotes") },
  handler: async (ctx, { noteId }) => {
    const note = await ctx.runQuery(internal.notes.getForRender, { noteId });
    if (!note || note.audioStatus === "ready") return "SKIPPED";

    const config = readCartesiaConfig(process.env);
    if (config === null || config === "INCOMPLETE") {
      await ctx.runMutation(internal.notes.setAudio, { noteId, status: "failed", error: "SPEECH_NOT_CONFIGURED" });
      return "SPEECH_NOT_CONFIGURED";
    }

    const result = await synthesizeSpeech({ requestId: `note-${noteId}`, text: note.spoken });
    if (result.ok && result.mode === "REAL_API") {
      const storageId = await ctx.storage.store(new Blob([Buffer.from(result.audioBase64, "base64")], { type: "audio/wav" }));
      await ctx.runMutation(internal.notes.setAudio, { noteId, status: "ready", storageId });
      return "READY";
    }
    if (!result.ok && isRetryable(result.errorCode, result.httpStatus)) {
      throw new Error(`Cartesia temporary failure: ${result.errorCode}${result.httpStatus ? ` ${result.httpStatus}` : ""}`);
    }
    const error = result.ok ? "SIMULATED" : `${result.errorCode}${result.httpStatus ? `_${result.httpStatus}` : ""}`;
    await ctx.runMutation(internal.notes.setAudio, { noteId, status: "failed", error });
    return error;
  },
});
