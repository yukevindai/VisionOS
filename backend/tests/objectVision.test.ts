import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { convexTest } from "convex-test";
import { api } from "../convex/_generated/api";
import schema from "../convex/schema";
import { findVisionObject, uprightJPEGDimensions, type ObjectVisionRequest } from "../convex/lib/objectVision";
import { MAX_RESPONSE_BYTES } from "../convex/lib/visionService";

// Synthetic JPEG headers and SIMULATED provider replies test boundaries, not recognition accuracy.
const modules = import.meta.glob("../convex/**/*.ts");
const jpeg = Buffer.from([0xff, 0xd8, 0xff, 0xc0, 0, 11, 8, 1, 224, 2, 128, 1, 1, 0x11, 0,
  0xff, 0xda, 0, 8, 1, 1, 0, 0, 63, 0, 0, 0xff, 0xd9]);
const args: ObjectVisionRequest = { imageBase64: jpeg.toString("base64"), query: "find my silver keys",
  frameId: "native-frame-1", capturedAtMs: 1_800_000_000_000, requestId: "object-1" };
const key = "SIMULATED-provider-key";
const selected = () => ({ status: "SELECTED", selectedIndex: 0,
  objects: [{ label: "silver keys", box_2d: [100, 200, 300, 400] }] });
const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
const envelope = (value: unknown) => ({ candidates: [{ finishReason: "STOP", content: { parts: [{ text: JSON.stringify(value) }] } }] });
const mockReply = (value: unknown) => vi.stubGlobal("fetch", vi.fn(async () => json(envelope(value))));
beforeEach(() => {
  vi.stubEnv("GEMINI_API_KEY", key);
  vi.stubEnv("GEMINI_MODEL", "gemini-SIMULATED");
  vi.stubEnv("GEMINI_THINKING_LEVEL", "");
  vi.stubEnv("GEMINI_OBJECT_MEDIA_RESOLUTION", "");
});
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.useRealTimers(); });
async function signedIn() {
  const t = convexTest(schema, modules);
  const owner = t.withIdentity({ issuer: "https://simulated-auth.invalid/", subject: "owner" });
  await owner.mutation(api.users.ensure, {});
  return { t, owner };
}

describe("native object recognition action", () => {
  it("requires an existing authenticated account before provider access", async () => {
    const t = convexTest(schema, modules);
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    await expect(t.action(api.vision.findObject, args)).rejects.toThrow("Authentication required");
    await expect(t.withIdentity({ issuer: "https://simulated-auth.invalid/", subject: "new" })
      .action(api.vision.findObject, args)).rejects.toThrow("Account setup required");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("sends the actual query/image and returns correlated identification without movement authorization", async () => {
    const { owner } = await signedIn();
    const fetch = vi.fn(async (_url: string, _init: RequestInit) => json(envelope(selected())));
    vi.stubGlobal("fetch", fetch);
    const result = await owner.action(api.vision.findObject, args);
    expect(result).toMatchObject({ requestId: args.requestId, mode: "REAL_API", status: "SELECTED",
      selectedObjectId: "object-1", screenModel: {
        frameId: args.frameId, capturedAtMs: args.capturedAtMs, schemaVersion: 3,
        controls: [{ id: "object-1", label: "silver keys", type: "other", legibility: "clear",
          region: { x: 0.2, y: 0.1, width: 0.2, height: 0.2 } }],
        image: { widthPx: 640, heightPx: 480, exifOrientation: 1, orientationApplied: true },
        evidence: { provider: "gemini", model: "gemini-SIMULATED", live: true },
      } });
    const request = fetch.mock.calls[0]!;
    expect(request[0]).toBe("https://generativelanguage.googleapis.com/v1beta/models/gemini-SIMULATED:generateContent");
    expect(request[1].headers).toEqual({ "Content-Type": "application/json", "x-goog-api-key": key });
    expect(request[1].redirect).toBe("error");
    const body = JSON.parse(request[1].body as string);
    expect(body.generationConfig).not.toHaveProperty("mediaResolution");
    expect(JSON.parse(body.contents[0].parts[0].text)).toEqual({ objectQuery: args.query });
    expect(body.contents[0].parts[1].inline_data.data).toBe(args.imageBase64);
    expect(JSON.stringify(result)).not.toContain(key);
    expect(JSON.stringify(result)).not.toMatch(/confidence|safety|haptic|direction/);
  });

  it("preserves ambiguity and absent objects without manufacturing a selection", async () => {
    for (const value of [{ status: "NOT_FOUND", selectedIndex: null, objects: [] },
      { status: "AMBIGUOUS", selectedIndex: null, objects: [...selected().objects,
        { label: "other silver keys", box_2d: [400, 500, 600, 700] }] }]) {
      mockReply(value);
      expect(await findVisionObject(args)).toMatchObject({ status: value.status, selectedObjectId: null });
    }
  });

  it("validates the owner request and JPEG metadata before network work", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    for (const change of [{ query: "" }, { query: " x " }, { query: "x".repeat(501) }, { query: "find\nkeys" },
      { query: "find\u0085keys" }, { query: "find\u202ekeys" }, { frameId: "" }, { capturedAtMs: -1 }, { imageBase64: "Zm9v" },
      { imageBase64: Buffer.from([0xff, 0xd8, 0xff]).toString("base64") }]) {
      await expect(findVisionObject({ ...args, ...change })).rejects.toThrow("INVALID_VISION");
    }
    expect(fetch).not.toHaveBeenCalled();
  });

  it("accepts upright EXIF and rejects rotated or truncated EXIF", () => {
    const exif = Buffer.from([0x45, 0x78, 0x69, 0x66, 0, 0, 0x49, 0x49, 42, 0, 8, 0, 0, 0,
      1, 0, 0x12, 1, 3, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0]);
    const segment = (data: Buffer) => Buffer.concat([jpeg.subarray(0, 2),
      Buffer.from([0xff, 0xe1, 0, data.length + 2]), data, jpeg.subarray(2)]).toString("base64");
    expect(uprightJPEGDimensions(segment(exif))).toEqual({ widthPx: 640, heightPx: 480 });
    const rotated = Buffer.from(exif); rotated[24] = 6;
    expect(() => uprightJPEGDimensions(segment(rotated))).toThrow("VISION_IMAGE_MUST_BE_UPRIGHT");
    expect(() => uprightJPEGDimensions(segment(exif.subarray(0, 20)))).toThrow("INVALID_VISION_IMAGE");
  });

  it("requires actual server configuration and never substitutes a model", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    for (const [name, value] of [["GEMINI_API_KEY", ""], ["GEMINI_MODEL", ""],
      ["GEMINI_MODEL", "../other"], ["GEMINI_THINKING_LEVEL", "invalid"]]) {
      vi.stubEnv(name!, value!);
      await expect(findVisionObject(args)).rejects.toThrow("VISION_NOT_CONFIGURED");
      vi.stubEnv("GEMINI_API_KEY", key); vi.stubEnv("GEMINI_MODEL", "gemini-SIMULATED"); vi.stubEnv("GEMINI_THINKING_LEVEL", "");
    }
    expect(fetch).not.toHaveBeenCalled();
  });

  it("passes an explicitly configured Gemini 3 thinking level", async () => {
    vi.stubEnv("GEMINI_MODEL", "gemini-3-SIMULATED"); vi.stubEnv("GEMINI_THINKING_LEVEL", "low");
    const fetch = vi.fn(async (_url: string, _init: RequestInit) => json(envelope(selected()))); vi.stubGlobal("fetch", fetch);
    await findVisionObject(args);
    expect(JSON.parse(fetch.mock.calls[0]![1].body as string).generationConfig.thinkingConfig).toEqual({ thinkingLevel: "low" });
  });

  it("applies only the explicitly configured object media resolution", async () => {
    for (const setting of ["low", "medium", "high"]) {
      vi.stubEnv("GEMINI_OBJECT_MEDIA_RESOLUTION", setting);
      const fetch = vi.fn(async (_url: string, _init: RequestInit) => json(envelope(selected())));
      vi.stubGlobal("fetch", fetch);
      await findVisionObject(args);
      const body = JSON.parse(fetch.mock.calls[0]![1].body as string);
      expect(body.generationConfig.mediaResolution).toBe(`MEDIA_RESOLUTION_${setting.toUpperCase()}`);
      expect(body.contents[0].parts[1].inline_data.data).toBe(args.imageBase64);
    }
  });

  it("rejects unknown media resolution before making a provider request", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    for (const setting of ["LOW", " low", "ultra_high", "automatic", "0"]) {
      vi.stubEnv("GEMINI_OBJECT_MEDIA_RESOLUTION", setting);
      await expect(findVisionObject(args)).rejects.toThrow("VISION_NOT_CONFIGURED");
    }
    expect(fetch).not.toHaveBeenCalled();
  });

  it("rejects malformed, inverted or out-of-image boxes and invalid selections", async () => {
    const bad = [
      { ...selected(), selectedIndex: 3 }, { ...selected(), status: "AMBIGUOUS" },
      { ...selected(), objects: [...selected().objects, { label: "other keys", box_2d: [400, 500, 600, 700] }] },
      { ...selected(), status: "NOT_FOUND", selectedIndex: null },
      ...[[0, 1, 0, 2], [-1, 0, 10, 20], [0, 0, 1001, 10], [0, 0, 1.5, 10]].map(box_2d =>
        ({ ...selected(), objects: [{ label: "keys", box_2d }] })),
      { ...selected(), objects: [{ ...selected().objects[0], label: "x".repeat(161) }] },
      { ...selected(), objects: Array(21).fill(selected().objects[0]) },
      { ...selected(), objects: [{ ...selected().objects[0], label: key }] },
    ];
    for (const value of bad) { mockReply(value); await expect(findVisionObject(args)).rejects.toThrow("INVALID_VISION_RESPONSE"); }
  });

  it("rejects truncation, blocking and malformed provider envelopes", async () => {
    for (const value of [{ candidates: [] }, { candidates: [{ finishReason: "MAX_TOKENS", content: { parts: [{ text: JSON.stringify(selected()) }] } }] },
      { candidates: [{ finishReason: "STOP", content: { parts: [{ text: "not JSON" }] } }] }]) {
      vi.stubGlobal("fetch", vi.fn(async () => json(value)));
      await expect(findVisionObject(args)).rejects.toThrow("INVALID_VISION_RESPONSE");
    }
  });

  it("bounds provider bytes and hides provider failures and credentials", async () => {
    for (const [response, code] of [
      [new Response(key, { status: 403 }), "VISION_SERVICE_UNAVAILABLE"],
      [new Response("x".repeat(MAX_RESPONSE_BYTES + 1), { headers: { "Content-Type": "application/json" } }), "VISION_RESPONSE_TOO_LARGE"],
    ] as const) {
      vi.stubGlobal("fetch", vi.fn(async () => response));
      await expect(findVisionObject(args)).rejects.toThrow(code);
    }
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error(key); }));
    await expect(findVisionObject(args)).rejects.toThrow("VISION_SERVICE_UNAVAILABLE");
  });

  it("ends even if a provider ignores abort, and discards its late result", async () => {
    vi.useFakeTimers();
    let respond!: (value: Response) => void;
    const fetch = vi.fn((_url: string, _init: RequestInit) => new Promise<Response>(resolve => { respond = resolve; }));
    vi.stubGlobal("fetch", fetch);
    const pending = expect(findVisionObject(args)).rejects.toThrow("VISION_TIMEOUT");
    await vi.advanceTimersByTimeAsync(60001);
    await pending;
    expect(fetch.mock.calls[0]![1].signal!.aborted).toBe(true);
    respond(json(envelope(selected())));
    await vi.runAllTimersAsync();
  });

  it("applies the deadline to a stalled response body, not just initial HTTP headers", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(new ReadableStream({ start() {} }),
      { headers: { "Content-Type": "application/json" } })));
    const pending = expect(findVisionObject(args)).rejects.toThrow("VISION_TIMEOUT");
    await vi.advanceTimersByTimeAsync(60001);
    await pending;
  });
});
