import { cronJobs } from "convex/server";
import { internal } from "./_generated/api";

const crons = cronJobs();
crons.interval("audit retired media rooms", { seconds: 10 }, internal.media.auditRetired, {});
export default crons;
