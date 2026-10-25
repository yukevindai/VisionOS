"use node";

import { v } from "convex/values";
import { action } from "./_generated/server";
import { api } from "./_generated/api";
import { readVisionScreen, validateVisionRequest, type VisionResponse } from "./lib/visionService";
import { findVisionObject, type ObjectVisionResponse } from "./lib/objectVision";

export const readScreen = action({
  args: { imageBase64: v.string(), frameId: v.string(), capturedAtMs: v.number(), requestId: v.string() },
  handler: async (ctx, args): Promise<VisionResponse> => {
    await ctx.runQuery(api.users.me, {});
    validateVisionRequest(args);
    return readVisionScreen(args);
  },
});

export const findObject = action({
  args: { imageBase64: v.string(), query: v.string(), frameId: v.string(), capturedAtMs: v.number(), requestId: v.string() },
  handler: async (ctx, args): Promise<ObjectVisionResponse> => {
    await ctx.runQuery(api.users.me, {});
    return findVisionObject(args);
  },
});
