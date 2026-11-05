import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";

const server = join(process.cwd(), ".next", "standalone", "server.js");
if (!existsSync(server)) {
  throw new Error("Standalone server not found. Run `bun run build` first.");
}

const child = spawn(process.execPath, [server], {
  stdio: "inherit",
  env: {
    ...process.env,
    NODE_ENV: "production",
    HOSTNAME: process.env.HOSTNAME || "0.0.0.0",
    PORT: process.env.PORT || "3000",
  },
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => child.kill(signal));
}
child.on("exit", (code) => process.exit(code ?? 0));
