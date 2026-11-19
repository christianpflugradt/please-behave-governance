import { execFileSync } from "node:child_process";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

/** The release runs on the dispatched commit of main, and main has not moved since. */
export function verifySource(env, cwd, git = execFileSync) {
  if (env.GITHUB_REF !== "refs/heads/main" || !/^[^/\s]+\/[^/\s]+$/.test(env.GITHUB_REPOSITORY ?? "")
      || !/^[0-9a-f]{40}$/.test(env.GITHUB_SHA ?? "")) {
    throw new Error("Invalid publication context");
  }
  const execution = { cwd, env, encoding: "utf8" };
  const head = git("git", ["rev-parse", "HEAD"], execution).trim();
  const remote = git("git", ["ls-remote", "origin", "refs/heads/main"], execution).trim().split(/\s+/)[0];
  if (head !== env.GITHUB_SHA || remote !== head) {
    throw new Error("main changed during the release; rerun from its latest commit.");
  }
  return head;
}

/** Local Semantic Release configuration would silently merge with the shared policy. */
export function assertNoLocalConfiguration(cwd) {
  const found = readdirSync(cwd).filter(name => /^release\.config\./.test(name) || /^\.releaserc/.test(name));
  try {
    if ("release" in JSON.parse(readFileSync(join(cwd, "package.json"), "utf8"))) found.push('package.json "release"');
  } catch {
    // A repository without a readable package.json has nothing to configure there.
  }
  if (found.length) {
    throw new Error(`Remove the local Semantic Release configuration (${found.join(", ")}); the shared release action owns it.`);
  }
}
