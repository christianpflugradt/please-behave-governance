import { execFileSync } from "node:child_process";

// Semantic Release starts a repository without release at 1.0.0 and has no setting for another version. Every new
// project starts at 0.1.0 instead, and the step to 1.0.0 stays a manual decision. Semantic Release continues from the
// highest release tag, so a seed tag of this version marks the start; the analyzer turns the first release into 0.1.0.
export const SEED_VERSION = "0.0.0";

const tagOf = tagFormat => tagFormat.replace("${version}", SEED_VERSION);

/** Tags the first commit locally when the repository has no release yet. The tag is removed before Semantic Release pushes. */
export function seedFirstRelease(tagFormat, cwd, env, run = execFileSync) {
  const git = (...args) => run("git", args, { cwd, env, encoding: "utf8" });
  if (git("tag", "--list", tagFormat.replace("${version}", "*")).trim()) return;
  const first = git("rev-list", "--max-parents=0", "HEAD").trim().split("\n").at(-1);
  git("tag", tagOf(tagFormat), first);
}

export function removeSeed(tagFormat, cwd, env, run = execFileSync) {
  const tag = tagOf(tagFormat);
  if (run("git", ["tag", "--list", tag], { cwd, env, encoding: "utf8" }).trim()) run("git", ["tag", "-d", tag], { cwd, env, stdio: "ignore" });
}

/** The seed must be gone before Semantic Release creates the release tag and pushes all tags. */
export const seedRemoval = {
  prepare(_pluginConfig, { cwd, env, options }) {
    removeSeed(options.tagFormat, cwd, env);
  },
};
