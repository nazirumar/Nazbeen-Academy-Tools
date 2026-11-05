import { cpSync, existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";

const root = process.cwd();
const nextDir = join(root, ".next");
const standalone = join(nextDir, "standalone");

if (!existsSync(standalone)) {
  throw new Error("Next.js standalone output was not produced. Check next.config.ts.");
}

mkdirSync(join(standalone, ".next"), { recursive: true });
cpSync(join(nextDir, "static"), join(standalone, ".next", "static"), {
  recursive: true,
});
if (existsSync(join(root, "public"))) {
  cpSync(join(root, "public"), join(standalone, "public"), { recursive: true });
}

console.log("Prepared .next/standalone with static assets and public files.");
