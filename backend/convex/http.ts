import { httpRouter } from "convex/server";
import { httpAction } from "./_generated/server";
import { internal } from "./_generated/api";

const http = httpRouter();
http.route({ path: "/livekit/webhook", method: "POST", handler: httpAction(async (ctx, request) => {
  const authorization = request.headers.get("Authorization");
  if (!authorization || authorization.length > 8_192) return new Response(null, { status: 401 });
  const reader = request.body?.getReader();
  if (!reader) return new Response(null, { status: 400 });
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const next = await reader.read();
      if (next.done) break;
      length += next.value.byteLength;
      if (length > 256 * 1024) { await reader.cancel(); return new Response(null, { status: 413 }); }
      chunks.push(next.value);
    }
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    const body = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    // Preserve exact raw text: the provider signature includes its hash.
    const status = await ctx.runAction(internal.livekit.receiveWebhook, { body, authorization });
    return new Response(null, { status });
  } catch { return new Response(null, { status: 503 }); }
}) });
export default http;
