import {cp, mkdir, readFile, rm, writeFile} from "node:fs/promises";
import {fileURLToPath} from "node:url";
import path from "node:path";

import {validateDashboardData} from "../public/core.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const publicDir = path.join(root, "public");
const distDir = path.join(root, "dist");

const html = await readFile(path.join(publicDir, "index.html"), "utf8");
for (const reference of ["./styles.css", "./app.js", "./core.js", "./data/jobs.json"]) {
  const diskPath = path.join(publicDir, reference.replace(/^\.\//, ""));
  await readFile(diskPath);
  if (reference !== "./core.js" && reference !== "./data/jobs.json" && !html.includes(reference)) {
    throw new Error(`index.html does not reference ${reference}`);
  }
}

const data = validateDashboardData(JSON.parse(await readFile(path.join(publicDir, "data", "jobs.json"), "utf8")));
if (data.sources.length !== 20 || data.stats.source_count !== 20) {
  throw new Error(`Production build requires exactly 20 sources; found ${data.sources.length}`);
}
if (!data.baseline_ready) throw new Error("Production build requires an initialized baseline");

await rm(distDir, {recursive: true, force: true});
await mkdir(distDir, {recursive: true});
await cp(publicDir, distDir, {recursive: true});
await writeFile(path.join(distDir, ".nojekyll"), "", "utf8");

console.log(`Built ${data.jobs.length} dashboard records from ${data.sources.length} sources into dist/`);
