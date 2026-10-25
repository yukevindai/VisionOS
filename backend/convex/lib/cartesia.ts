"use node";

import { Buffer } from "node:buffer";
import { ConvexError } from "convex/values";
import { SPEED_RANGE, VOLUME_RANGE } from "./speechLimits";

// R3 Cartesia text-to-speech adapter. It only turns text into audio bytes.
// It never plays audio, schedules output, or decides guidance: R1 owns playback
// and priority. Callers must fall back to on-device speech when ok === false.

export const CARTESIA_URL = "https://api.cartesia.ai/tts/bytes";
export const CARTESIA_VERSION = "2026-03-01";
export const MAX_TEXT_CHARS = 300;
export const MAX_AUDIO_BYTES = 4 * 1024 * 1024;
export const TIMEOUT_MS = 5_000; // spec §5: cloud timeout 5 s
export const SAMPLE_RATE = 24_000;

/**
 * voiceId replaces the configured voice (a helper's own cloned voice, Family Notes only).
 * speed and volume map to Cartesia generation_config; omitted means the provider default.
 */
export type SpeechRequest = { requestId: string; text: string; voiceId?: string; speed?: number; volume?: number };
export { SPEED_RANGE, VOLUME_RANGE };
export type SpeechErrorCode = "SPEECH_NOT_CONFIGURED" | "PROVIDER_TIMEOUT" | "PROVIDER_ERROR" | "INVALID_PROVIDER_AUDIO";
export type SpeechResult =
  | { ok: true; requestId: string; mode: "REAL_API" | "SIMULATED"; format: "wav"; sampleRate: number;
      audioBase64: string; characters: number; latencyMs: number }
  | { ok: false; requestId: string; errorCode: SpeechErrorCode; httpStatus?: number; latencyMs: number };

export type CartesiaConfig = { apiKey: string; voiceId: string; model: string };
type Env = Record<string, string | undefined>;
type Deps = { env?: Env; fetch?: typeof fetch; now?: () => number };

function identifier(value: string): boolean {
  return value.length > 0 && value.length <= 128 && value.trim().length > 0 && !/[\u0000-\u001f\u007f]/.test(value);
}

/** Throws INVALID_SPEECH_REQUEST for caller mistakes; these are not provider failures. */
export function validateSpeechRequest(req: SpeechRequest): string {
  const text = req.text.trim();
  const inRange = (value: number | undefined, [lo, hi]: readonly [number, number]) =>
    value === undefined || (Number.isFinite(value) && value >= lo && value <= hi);
  if ((req.voiceId !== undefined && !identifier(req.voiceId)) || !inRange(req.speed, SPEED_RANGE) ||
      !inRange(req.volume, VOLUME_RANGE)) {
    throw new ConvexError({ code: "INVALID_SPEECH_REQUEST" });
  }
  if (!identifier(req.requestId) || text.length === 0 || text.length > MAX_TEXT_CHARS || /[\u0000-\u0008\u000b-\u001f\u007f]/.test(text)) {
    throw new ConvexError({ code: "INVALID_SPEECH_REQUEST" });
  }
  return text;
}

/**
 * No API key → null (SIMULATED mode). A key without voice/model is a setup error,
 * because the spec forbids inventing model or voice values (§0 rule 1).
 */
export function readCartesiaConfig(env: Env): CartesiaConfig | null | "INCOMPLETE" {
  const apiKey = env.CARTESIA_API_KEY?.trim();
  if (!apiKey) return null;
  const voiceId = env.CARTESIA_VOICE_ID?.trim();
  const model = env.CARTESIA_MODEL?.trim();
  if (!voiceId || !model) return "INCOMPLETE";
  return { apiKey, voiceId, model };
}

/** A short silent mono 16-bit WAV, clearly marked as simulated output. */
export function simulatedWav(durationMs = 200): Buffer {
  const samples = Math.round((SAMPLE_RATE * durationMs) / 1000);
  const dataBytes = samples * 2;
  const wav = Buffer.alloc(44 + dataBytes);
  wav.write("RIFF", 0, "ascii"); wav.writeUInt32LE(36 + dataBytes, 4); wav.write("WAVE", 8, "ascii");
  wav.write("fmt ", 12, "ascii"); wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(SAMPLE_RATE, 24); wav.writeUInt32LE(SAMPLE_RATE * 2, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
  wav.write("data", 36, "ascii"); wav.writeUInt32LE(dataBytes, 40);
  return wav;
}

export function isWav(bytes: Uint8Array): boolean {
  return bytes.length > 44 && Buffer.from(bytes.subarray(0, 4)).toString("ascii") === "RIFF" &&
    Buffer.from(bytes.subarray(8, 12)).toString("ascii") === "WAVE";
}

export async function synthesizeSpeech(req: SpeechRequest, deps: Deps = {}): Promise<SpeechResult> {
  const text = validateSpeechRequest(req);
  const env = deps.env ?? process.env;
  const now = deps.now ?? Date.now;
  const started = now();
  const latency = () => now() - started;
  const config = readCartesiaConfig(env);

  if (config === null) {
    return { ok: true, requestId: req.requestId, mode: "SIMULATED", format: "wav", sampleRate: SAMPLE_RATE,
      audioBase64: simulatedWav().toString("base64"), characters: text.length, latencyMs: latency() };
  }
  if (config === "INCOMPLETE") return { ok: false, requestId: req.requestId, errorCode: "SPEECH_NOT_CONFIGURED", latencyMs: latency() };

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await (deps.fetch ?? fetch)(CARTESIA_URL, {
      method: "POST",
      redirect: "error",
      signal: controller.signal,
      headers: {
        Authorization: `Bearer ${config.apiKey}`,
        "Cartesia-Version": CARTESIA_VERSION,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model_id: config.model,
        transcript: text,
        voice: { mode: "id", id: req.voiceId ?? config.voiceId },
        language: "en",
        ...(req.speed !== undefined || req.volume !== undefined
          ? { generation_config: { ...(req.speed !== undefined ? { speed: req.speed } : {}),
              ...(req.volume !== undefined ? { volume: req.volume } : {}) } }
          : {}),
        // WAV, not raw PCM: the iOS app plays provider audio with AVAudioPlayer(data:).
        output_format: { container: "wav", encoding: "pcm_s16le", sample_rate: SAMPLE_RATE },
      }),
    });
    if (!res.ok) {
      return { ok: false, requestId: req.requestId, errorCode: "PROVIDER_ERROR", httpStatus: res.status, latencyMs: latency() };
    }
    const length = Number(res.headers.get("content-length") ?? "0");
    if (length > MAX_AUDIO_BYTES) return { ok: false, requestId: req.requestId, errorCode: "INVALID_PROVIDER_AUDIO", latencyMs: latency() };
    const audio = new Uint8Array(await res.arrayBuffer());
    if (audio.length > MAX_AUDIO_BYTES || !isWav(audio)) {
      return { ok: false, requestId: req.requestId, errorCode: "INVALID_PROVIDER_AUDIO", latencyMs: latency() };
    }
    return { ok: true, requestId: req.requestId, mode: "REAL_API", format: "wav", sampleRate: SAMPLE_RATE,
      audioBase64: Buffer.from(audio).toString("base64"), characters: text.length, latencyMs: latency() };
  } catch {
    // Timeout and network failure both mean "speak this on-device instead".
    return { ok: false, requestId: req.requestId, errorCode: "PROVIDER_TIMEOUT", latencyMs: latency() };
  } finally {
    clearTimeout(timer);
  }
}

// ---------- voice cloning (Family Notes: a helper's own voice, with consent) ----------

export const CLONE_URL = "https://api.cartesia.ai/voices/clone";
export const VOICES_URL = "https://api.cartesia.ai/voices";
export const CLONE_TIMEOUT_MS = 30_000;
export const MIN_CLONE_SECONDS = 10;   // Cartesia: about 10 s is enough to start
export const MAX_CLONE_SECONDS = 60;   // Cartesia: up to 60 s retains the accent (Sonic 3.6+)
export const MAX_CLONE_BYTES = 6 * 1024 * 1024;

export type CloneResult = { ok: true; voiceId: string } | { ok: false; errorCode: string; httpStatus?: number };

/** Duration of a PCM WAV (any rate, mono or stereo, 16-bit), or null when it is not one. */
export function wavSeconds(bytes: Uint8Array): number | null {
  if (!isWav(bytes)) return null;
  const view = Buffer.from(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let offset = 12, byteRate = 0, dataBytes = 0;
  while (offset + 8 <= view.length) {
    const id = view.toString("ascii", offset, offset + 4);
    const size = view.readUInt32LE(offset + 4);
    if (id === "fmt " && offset + 16 <= view.length) {
      if (view.readUInt16LE(offset + 8) !== 1 || view.readUInt16LE(offset + 22) !== 16) return null; // PCM s16 only
      byteRate = view.readUInt32LE(offset + 16);
    } else if (id === "data") {
      dataBytes = Math.min(size, view.length - offset - 8);
      break;
    }
    offset += 8 + size + (size % 2);
  }
  return byteRate > 0 && dataBytes > 0 ? dataBytes / byteRate : null;
}

/** Instant Voice Clone from one consented recording. The voice is private to our Cartesia account. */
export async function cloneVoice(clip: Uint8Array, name: string, description: string,
  deps: Deps = {}): Promise<CloneResult> {
  const config = readCartesiaConfig(deps.env ?? process.env);
  if (config === null || config === "INCOMPLETE") return { ok: false, errorCode: "SPEECH_NOT_CONFIGURED" };
  const form = new FormData();
  form.append("clip", new Blob([clip.slice()], { type: "audio/wav" }), "consented-recording.wav");
  form.append("name", name.slice(0, 100));
  form.append("description", description.slice(0, 500));
  form.append("language", "en");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), CLONE_TIMEOUT_MS);
  try {
    const res = await (deps.fetch ?? fetch)(CLONE_URL, {
      method: "POST", redirect: "error", signal: controller.signal, body: form,
      headers: { Authorization: `Bearer ${config.apiKey}`, "Cartesia-Version": CARTESIA_VERSION },
    });
    if (!res.ok) return { ok: false, errorCode: "PROVIDER_ERROR", httpStatus: res.status };
    const body = await res.json() as { id?: unknown };
    return typeof body.id === "string" && identifier(body.id)
      ? { ok: true, voiceId: body.id } : { ok: false, errorCode: "INVALID_PROVIDER_RESPONSE" };
  } catch {
    return { ok: false, errorCode: "PROVIDER_TIMEOUT" };
  } finally {
    clearTimeout(timer);
  }
}

/** Deletes a cloned voice at Cartesia. An already-missing voice counts as deleted. */
export async function deleteVoice(voiceId: string, deps: Deps = {}): Promise<{ ok: boolean; httpStatus?: number }> {
  const config = readCartesiaConfig(deps.env ?? process.env);
  if (config === null || config === "INCOMPLETE") return { ok: false };
  if (!identifier(voiceId)) return { ok: false };
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await (deps.fetch ?? fetch)(`${VOICES_URL}/${encodeURIComponent(voiceId)}`, {
      method: "DELETE", redirect: "error", signal: controller.signal,
      headers: { Authorization: `Bearer ${config.apiKey}`, "Cartesia-Version": CARTESIA_VERSION },
    });
    return { ok: res.ok || res.status === 404, httpStatus: res.status };
  } catch {
    return { ok: false };
  } finally {
    clearTimeout(timer);
  }
}
