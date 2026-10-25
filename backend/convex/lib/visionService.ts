"use node";

import { Buffer } from "node:buffer";
import { ConvexError, type Value } from "convex/values";

export const MAX_IMAGE_BYTES = 512 * 1024;
export const MAX_RESPONSE_BYTES = 256 * 1024;
const MAX_ENCODED_LENGTH = 4 * Math.ceil(MAX_IMAGE_BYTES / 3);
export type VisionRequest = { imageBase64: string; frameId: string; capturedAtMs: number; requestId: string };
export type VisionResponse = { requestId: string; screenModel: Record<string, Value> };

function fail(code: string): never { throw new ConvexError({ code }); }
function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function identifier(value: string): boolean {
  return value.length > 0 && value.length <= 128 && value.trim().length > 0 && !/[\u0000-\u001f\u007f]/.test(value);
}

export function validateVisionRequest(args: VisionRequest): void {
  if (!identifier(args.frameId) || !identifier(args.requestId) || !Number.isSafeInteger(args.capturedAtMs) || args.capturedAtMs < 0) {
    fail("INVALID_VISION_REQUEST");
  }
  if (!args.imageBase64 || args.imageBase64.length > MAX_ENCODED_LENGTH) {
    fail("INVALID_VISION_IMAGE");
  }
  const bytes = Buffer.from(args.imageBase64, "base64");
  if (bytes.length > MAX_IMAGE_BYTES || bytes.toString("base64") !== args.imageBase64) fail("INVALID_VISION_IMAGE");
  const jpeg = bytes.length >= 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff;
  const png = bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  if (!jpeg && !png) fail("INVALID_VISION_IMAGE");
}

export function readVisionConfig(): { url: string; token: string } {
  const value = process.env.R3_VISION_URL;
  const token = process.env.R3_VISION_SERVICE_TOKEN;
  if (!value || !token || token.length < 32 || token.trim() !== token || /[^\x20-\x7e]/.test(token)) fail("VISION_NOT_CONFIGURED");
  let url: URL;
  try { url = new URL(value); } catch { return fail("VISION_NOT_CONFIGURED"); }
  if (url.protocol !== "https:" || url.username || url.password || url.hash || url.search || url.pathname !== "/v1/read-screen") {
    fail("VISION_NOT_CONFIGURED");
  }
  return { url: url.href, token };
}

export function validateVisionResponse(value: unknown, args: VisionRequest): VisionResponse {
  if (!record(value) || value.requestId !== args.requestId || !record(value.screenModel)) fail("INVALID_VISION_RESPONSE");
  const model = value.screenModel;
  if (model.schemaVersion !== 3 || model.frameId !== args.frameId || model.capturedAtMs !== args.capturedAtMs ||
      model.coordinateSystem !== "normalizedImageTopLeft" || model.source !== "visionModel" ||
      !record(model.evidence) || model.evidence.provider !== "gemini" || model.evidence.live !== true ||
      typeof model.evidence.model !== "string" ||
      typeof model.panelType !== "string" || !["available", "partial", "unavailable"].includes(String(model.availability)) ||
      typeof model.isPaymentScreen !== "boolean" || !Array.isArray(model.controls) || model.controls.length > 200 ||
      !Array.isArray(model.sections) || model.sections.length > 50 || !model.sections.every(s => typeof s === "string")) {
    fail("INVALID_VISION_RESPONSE");
  }
  // Optional kiosk step evidence (Step 6). Absent on older services; type-checked when present.
  if ((model.screenKind !== undefined && (typeof model.screenKind !== "string" || model.screenKind.length > 32)) ||
      (model.isWelcomeScreen !== undefined && typeof model.isWelcomeScreen !== "boolean") ||
      (model.orderTotal !== undefined && (typeof model.orderTotal !== "string" || model.orderTotal.length > 120)) ||
      (model.screenSignature !== undefined && (typeof model.screenSignature !== "string" ||
        !/^scr-[0-9a-f]{16}$/.test(model.screenSignature)))) {
    fail("INVALID_VISION_RESPONSE");
  }
  for (const control of model.controls) {
    if (!record(control) || typeof control.id !== "string" || typeof control.label !== "string" ||
        typeof control.type !== "string" || typeof control.legibility !== "string") fail("INVALID_VISION_RESPONSE");
    if (control.region !== undefined) {
      const region = control.region;
      if (!record(region) || !["x", "y", "width", "height"].every(key =>
        typeof region[key] === "number" && Number.isFinite(region[key]))) fail("INVALID_VISION_RESPONSE");
    }
  }
  return { requestId: args.requestId, screenModel: model as Record<string, Value> };
}

export async function readVisionScreen(args: VisionRequest): Promise<VisionResponse> {
  const config = readVisionConfig();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 6_000);
  try {
    const response = await fetch(config.url, {
      method: "POST", redirect: "error", signal: controller.signal,
      headers: { "Authorization": `Bearer ${config.token}`, "Content-Type": "application/json" },
      body: JSON.stringify(args),
    });
    if (!response.ok || !response.headers.get("Content-Type")?.toLowerCase().startsWith("application/json")) fail("VISION_SERVICE_UNAVAILABLE");
    const reader = response.body?.getReader();
    if (!reader) fail("INVALID_VISION_RESPONSE");
    const chunks: Uint8Array[] = [];
    let size = 0;
    while (true) {
      const next = await reader.read();
      if (next.done) break;
      size += next.value.byteLength;
      if (size > MAX_RESPONSE_BYTES) { await reader.cancel(); fail("VISION_RESPONSE_TOO_LARGE"); }
      chunks.push(next.value);
    }
    const bytes = Buffer.concat(chunks);
    let value: unknown;
    try { value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); }
    catch { return fail("INVALID_VISION_RESPONSE"); }
    return validateVisionResponse(value, args);
  } catch (error) {
    if (controller.signal.aborted) fail("VISION_TIMEOUT");
    if (error instanceof ConvexError) throw error;
    return fail("VISION_SERVICE_UNAVAILABLE");
  } finally { clearTimeout(timer); }
}
