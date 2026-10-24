import { defineSchema } from "convex/server";
import { authTables } from "./authTables";
import { sessionTables } from "./sessionTables";
import { mediaTables } from "./mediaTables";
import { inviteLimitTables } from "./inviteLimitTables";
import { mediaRequestLimitTables } from "./mediaRequestLimitTables";

export default defineSchema({ ...authTables, ...sessionTables, ...mediaTables, ...inviteLimitTables, ...mediaRequestLimitTables });
