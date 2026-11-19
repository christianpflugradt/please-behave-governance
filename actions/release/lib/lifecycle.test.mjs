// Runs Semantic Release for real against a local repository, in a file of its own because Semantic Release and its
// child processes write to the standard streams that the test runner uses.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { PassThrough } from "node:stream";
import test from "node:test";
import semanticRelease from "semantic-release";
import { publish } from "./publish.mjs";

test("the hooks run in the Semantic Release lifecycle against a real repository", async () => {
  const dir = mkdtempSync(join(tmpdir(), "lifecycle-"));
  const git = (...args) => execFileSync("git", args, { cwd: join(dir, "work"), encoding: "utf8" });
  execFileSync("git", ["init", "-q", "--bare", "-b", "main", join(dir, "origin.git")]); // CI defaults to master
  execFileSync("git", ["init", "-q", "-b", "main", join(dir, "work")]);
  for (const [key, value] of [["user.email", "t@t"], ["user.name", "t"]]) git("config", key, value);
  git("remote", "add", "origin", join(dir, "origin.git"));
  writeFileSync(join(dir, "work", "a"), "a");
  git("add", ".");
  git("commit", "-qm", "feat: first");
  git("tag", "v1.0.0");
  writeFileSync(join(dir, "work", "b"), "b");
  git("add", ".");
  git("commit", "-qm", "fix: second");
  git("push", "-q", "origin", "main", "--tags");
  const log = join(dir, "log");
  // Spawned processes must not join the test runner protocol, and CI tokens would turn the remote URL into an authenticated one.
  const baseEnv = Object.fromEntries(
    Object.entries(process.env).filter(([key]) => !/^(NODE_TEST_CONTEXT|GITHUB_TOKEN|GH_TOKEN|GITHUB_ACTIONS|GIT_CREDENTIALS)$/.test(key)),
  );
  const lifecycleEnv = {
    ...baseEnv,
    GITHUB_REF: "refs/heads/main",
    GITHUB_REPOSITORY: "owner/repo",
    GITHUB_SHA: git("rev-parse", "HEAD").trim(),
    EXPECTED_RELEASE_VERSION: "1.0.1",
    GIT_CONFIG_COUNT: "1",
    GIT_CONFIG_KEY_0: `url.${join(dir, "origin.git")}.insteadOf`,
    GIT_CONFIG_VALUE_0: "https://github.com/owner/repo.git",
  };
  // Without the GitHub plugin, which needs the API, and as a real rather than a dry run.
  // Its log goes to a sink, so it does not mix with the test runner's output.
  const release = (config, context) =>
    semanticRelease(
      { ...config, noCi: true, plugins: config.plugins.filter(plugin => plugin[0] !== "@semantic-release/github") },
      { ...context, stdout: new PassThrough(), stderr: new PassThrough() },
    );
  // Git reads the rewrite of the GitHub URL to the local remote from the process environment.
  Object.assign(process.env, { GIT_CONFIG_COUNT: "1", GIT_CONFIG_KEY_0: lifecycleEnv.GIT_CONFIG_KEY_0, GIT_CONFIG_VALUE_0: lifecycleEnv.GIT_CONFIG_VALUE_0 });
  try {
    await publish(lifecycleEnv, release, execFileSync, join(dir, "work"), {
      verify: `echo verify >> ${log}`,
      prepare: `echo prepare >> ${log}`,
      publish: `echo "publish $EXPECTED_RELEASE_VERSION" >> ${log}`,
    });
  } finally {
    for (const key of ["GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"]) delete process.env[key];
  }
  assert.deepEqual(readFileSync(log, "utf8").trim().split("\n"), ["verify", "prepare", "verify", "publish 1.0.1"]);
  assert.match(git("ls-remote", "--tags", "origin"), /refs\/tags\/v1\.0\.1/);
});
