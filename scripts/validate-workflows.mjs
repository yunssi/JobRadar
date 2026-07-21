import {readdir, readFile} from "node:fs/promises";
import {fileURLToPath} from "node:url";
import path from "node:path";

import {parseDocument} from "yaml";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const workflowsDir = path.join(root, ".github", "workflows");
const filenames = (await readdir(workflowsDir)).filter((name) => name.endsWith(".yml") || name.endsWith(".yaml"));

if (!filenames.length) throw new Error("No GitHub Actions workflows were found");

for (const filename of filenames) {
  const source = await readFile(path.join(workflowsDir, filename), "utf8");
  const document = parseDocument(source, {prettyErrors: true, uniqueKeys: true});
  if (document.errors.length) {
    throw new Error(`${filename}: ${document.errors.map((error) => error.message).join("; ")}`);
  }
  const workflow = document.toJS();
  if (!workflow || typeof workflow !== "object" || !("on" in workflow) || !("jobs" in workflow)) {
    throw new Error(`${filename}: workflow must define on and jobs`);
  }
  if (!workflow.jobs || typeof workflow.jobs !== "object" || !Object.keys(workflow.jobs).length) {
    throw new Error(`${filename}: workflow must define at least one job`);
  }
}

console.log(`Validated ${filenames.length} GitHub Actions workflow files`);
