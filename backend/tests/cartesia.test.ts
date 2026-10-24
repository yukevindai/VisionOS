import { afterEach, describe, expect, it, vi } from "vitest";
import { Buffer } from "node:buffer";
import {
  CARTESIA_URL, CARTESIA_VERSION, MAX_TEXT_CHARS, isWav, readCartesiaConfig, simulatedWav, synthesizeSpeech,
} from "../convex/lib/cartesia";

// All provider replies here are SIMULATED. No test calls Cartesia or spends credits.
const KEY = "SIMULATED-cartesia-key-0000";
const env = { CARTESIA_API_KEY: KEY, CARTESIA_VOICE_ID: "SIMULATED-voice", CARTESIA_MODEL: "SIMULATED-model" };
const req = { requestId: "speech-1", text: "There are three options: Burgers, Drinks, and Desserts." };
const wavReply = () => new Response(new Uint8Array(simulatedWav()), { headers: { "Content-Type": "audio/wav" } });

afterEach(() => { vi.useRealTimers(); });

describe("Cartesia TTS adapter", () => {
  it("returns labelled SIMULATED silence when no key is configured, without network", async () => {
    const fetch = vi.fn();
    const result = await synthesizeSpeech(req, { env: {}, fetch });
    expect(fetch).not.toHaveBeenCalled();
    expect(result).toMatchObject({ ok: true, mode: "SIMULATED", format: "wav", requestId: "speech-1" });
    if (result.ok) expect(isWav(Buffer.from(result.audioBase64, "base64"))).toBe(true);
  });

  it("refuses to guess a voice or model when only the key is set", async () => {
    expect(readCartesiaConfig({ CARTESIA_API_KEY: KEY })).toBe("INCOMPLETE");
    const fetch = vi.fn();
    const result = await synthesizeSpeech(req, { env: { CARTESIA_API_KEY: KEY }, fetch });
    expect(result).toMatchObject({ ok: false, errorCode: "SPEECH_NOT_CONFIGURED" });
    expect(fetch).not.toHaveBeenCalled();
  });

  it("sends the documented request and returns WAV audio", async () => {
    const fetch = vi.fn(async (_u: string, _i: RequestInit) => wavReply());
    const result = await synthesizeSpeech(req, { env, fetch: fetch as unknown as typeof globalThis.fetch });
    expect(result).toMatchObject({ ok: true, mode: "REAL_API", sampleRate: 24000, characters: req.text.length });
    const [url, init] = fetch.mock.calls[0]!;
    expect(url).toBe(CARTESIA_URL);
    expect(init).toMatchObject({ method: "POST", redirect: "error" });
    expect(init.headers).toEqual({ Authorization: `Bearer ${KEY}`, "Cartesia-Version": CARTESIA_VERSION, "Content-Type": "application/json" });
    expect(JSON.parse(init.body as string)).toEqual({
      model_id: "SIMULATED-model", transcript: req.text, voice: { mode: "id", id: "SIMULATED-voice" }, language: "en",
      output_format: { container: "wav", encoding: "pcm_s16le", sample_rate: 24000 },
    });
  });

  it("reports HTTP errors with their status and never leaks the key", async () => {
    const fetch = vi.fn(async () => new Response(`bad key ${KEY}`, { status: 401 }));
    const result = await synthesizeSpeech(req, { env, fetch: fetch as unknown as typeof globalThis.fetch });
    expect(result).toMatchObject({ ok: false, errorCode: "PROVIDER_ERROR", httpStatus: 401 });
    expect(JSON.stringify(result)).not.toContain(KEY);
  });

  it("maps network failure and timeout to PROVIDER_TIMEOUT so the app can fall back", async () => {
    const down = vi.fn(async () => { throw new TypeError("network down"); });
    expect(await synthesizeSpeech(req, { env, fetch: down as unknown as typeof globalThis.fetch }))
      .toMatchObject({ ok: false, errorCode: "PROVIDER_TIMEOUT" });

    vi.useFakeTimers();
    const hang = vi.fn((_u: string, init: RequestInit) => new Promise<Response>((_, reject) => {
      init.signal!.addEventListener("abort", () => reject(new Error("aborted")));
    }));
    const pending = synthesizeSpeech(req, { env, fetch: hang as unknown as typeof globalThis.fetch });
    await vi.advanceTimersByTimeAsync(5_000);
    expect(await pending).toMatchObject({ ok: false, errorCode: "PROVIDER_TIMEOUT" });
  });

  it("rejects audio that is not WAV", async () => {
    const fetch = vi.fn(async () => new Response(new Uint8Array(100)));
    expect(await synthesizeSpeech(req, { env, fetch: fetch as unknown as typeof globalThis.fetch }))
      .toMatchObject({ ok: false, errorCode: "INVALID_PROVIDER_AUDIO" });
  });

  it("rejects bad requests before any provider work", async () => {
    const fetch = vi.fn();
    for (const bad of [{ ...req, text: "  " }, { ...req, text: "x".repeat(MAX_TEXT_CHARS + 1) }, { ...req, requestId: "" }]) {
      await expect(synthesizeSpeech(bad, { env, fetch })).rejects.toThrow();
    }
    expect(fetch).not.toHaveBeenCalled();
  });
});
