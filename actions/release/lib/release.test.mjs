// Offline tests: run with `node --test` in this directory (needs `npm ci` once).
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { analysisPlugins, createConfig } from "./config.mjs";
import { assetsFrom, settings } from "./plan.mjs";
import { publish } from "./publish.mjs";
import { assertNoLocalConfiguration, verifySource } from "./source.mjs";
import { developmentVersion, stableFromTag } from "./version.mjs";

const SHA = "a".repeat(40);
const env = { GITHUB_REF: "refs/heads/main", GITHUB_REPOSITORY: "owner/repo", GITHUB_SHA: SHA };
const fakeGit = (head, remote) => (_command, args) => (args[0] === "rev-parse" ? `${head}\n` : `${remote}\trefs/heads/main\n`);

test("stable version is read from the tag with or without a prefix", () => {
  assert.equal(stableFromTag("v1.2.3", "v${version}"), "1.2.3");
  assert.equal(stableFromTag("1.2.3", "${version}"), "1.2.3");
  assert.throws(() => stableFromTag("1.2.3", "v${version}"));
  assert.throws(() => stableFromTag("v1.2.3-dev.1", "v${version}"));
});

test("development versions count up per Berlin day", () => {
  const now = new Date("2026-11-19T10:00:00Z");
  assert.equal(developmentVersion("1.2.3", [], now), "1.2.3-dev.20261119.1");
  const releases = [{ tag_name: "dev-1.2.3-dev.20261119.1" }, { tag_name: "dev-1.2.3-dev.20261119.4" }, { tag_name: "dev-1.2.3-dev.20261118.9" }];
  assert.equal(developmentVersion("1.2.3", releases, now), "1.2.3-dev.20261119.5");
});

test("publication requires the dispatched commit and an unmoved main", () => {
  assert.equal(verifySource(env, ".", fakeGit(SHA, SHA)), SHA);
  assert.throws(() => verifySource(env, ".", fakeGit(SHA, "b".repeat(40))), /main changed/);
  assert.throws(() => verifySource({ ...env, GITHUB_REF: "refs/heads/x" }, ".", fakeGit(SHA, SHA)), /Invalid publication context/);
});

test("local Semantic Release configuration is rejected", () => {
  const dir = mkdtempSync(join(tmpdir(), "release-"));
  assertNoLocalConfiguration(dir);
  writeFileSync(join(dir, "release.config.mjs"), "export default {};\n");
  assert.throws(() => assertNoLocalConfiguration(dir), /release\.config\.mjs/);
});

test("inputs have defaults and are validated", () => {
  assert.deepEqual(settings({ GITHUB_REPOSITORY: "o/r" }), {
    mode: "release",
    tagFormat: "v${version}",
    assets: [],
    force: false,
    repository: "o/r",
    hooks: { verify: "", prepare: "", publish: "" },
  });
  assert.equal(settings({ RELEASE_FORCE: "true" }).force, true);
  assert.deepEqual(settings({ RELEASE_ASSETS: "dist/*.zip\n\n dist/SHA256SUMS \n" }).assets, ["dist/*.zip", "dist/SHA256SUMS"]);
  assert.throws(() => settings({ RELEASE_MODE: "nightly" }));
});

test("force turns a non-releasing analysis into a patch and leaves releases alone", async () => {
  const [[forcing]] = analysisPlugins({ force: true });
  const context = { commits: [{ message: "docs: text", hash: "1" }], logger: { log() {} }, cwd: "." };
  assert.equal(await forcing.analyzeCommits({ preset: "conventionalcommits" }, context), "patch");
  const feature = { ...context, commits: [{ message: "feat: add", hash: "2" }] };
  assert.equal(await forcing.analyzeCommits({ preset: "conventionalcommits" }, feature), "minor");
  assert.equal(analysisPlugins()[0][0], "@semantic-release/commit-analyzer");
});

test("the configuration derives the repository and keeps the guard before GitHub", () => {
  const guard = { verifyRelease() {} };
  const config = createConfig({ repository: "owner/repo", tagFormat: "v${version}", assets: ["dist/*"], guard });
  assert.equal(config.repositoryUrl, "https://github.com/owner/repo.git");
  assert.equal(config.plugins[2], guard);
  assert.equal(config.plugins[3][0], "@semantic-release/github");
});

// Publication: gh, git and Semantic Release are replaced, so nothing leaves the machine.
const publishEnv = { ...env, EXPECTED_RELEASE_VERSION: "1.2.4", RELEASE_ASSETS: "dist/*.zip" };
const scriptedRun = (calls, { latest = { tag_name: "v1.2.3" }, releases = [] } = {}) => (command, args, options) => {
  calls.push([command, ...args]);
  if (command === "git") return fakeGit(SHA, SHA)(command, args, options);
  return JSON.stringify(args.includes("--paginate") ? [releases] : latest);
};
const workdir = () => {
  const dir = mkdtempSync(join(tmpdir(), "publish-"));
  mkdirSync(join(dir, "dist"));
  writeFileSync(join(dir, "dist", "app.zip"), "zip");
  return dir;
};

test("a release is refused when the version changed after planning", async () => {
  const cwd = workdir();
  const release = version => async (config, context) => {
    config.plugins[2].verifyRelease({}, { nextRelease: { version }, env: context.env, cwd });
    return { nextRelease: { version } };
  };
  await publish(publishEnv, release("1.2.4"), scriptedRun([]), cwd);
  await assert.rejects(publish(publishEnv, release("1.3.0"), scriptedRun([]), cwd), /changed after planning/);
  await assert.rejects(publish(publishEnv, async () => undefined, scriptedRun([]), cwd), /disappeared/);
});

test("a prerelease creates a dev release with the matched assets", async () => {
  const cwd = workdir();
  const version = developmentVersion("1.2.3", []);
  const calls = [];
  await publish({ ...publishEnv, RELEASE_MODE: "prerelease", EXPECTED_RELEASE_VERSION: version }, undefined, scriptedRun(calls), cwd);
  const create = calls.find(call => call[1] === "release");
  assert.deepEqual(create.slice(0, 5), ["gh", "release", "create", `dev-${version}`, "dist/app.zip"]);
  assert.ok(create.includes("--prerelease") && create.includes(SHA));
});

test("a prerelease is refused for a stale version or without assets", async () => {
  const cwd = workdir();
  const prerelease = { ...publishEnv, RELEASE_MODE: "prerelease" };
  await assert.rejects(publish(prerelease, undefined, scriptedRun([]), cwd), /changed after planning/);
  const version = developmentVersion("1.2.3", []);
  await assert.rejects(publish({ ...prerelease, EXPECTED_RELEASE_VERSION: version, RELEASE_ASSETS: "nothing/*" }, undefined, scriptedRun([]), cwd), /No release assets/);
});

test("asset lines may carry a label", () => {
  assert.deepEqual(assetsFrom("dist/app.jar :: Executable JAR\ndist/SHA256SUMS\n"), [{ path: "dist/app.jar", label: "Executable JAR" }, "dist/SHA256SUMS"]);
});

test("a prerelease verifies first and labels its assets", async () => {
  const cwd = workdir();
  const version = developmentVersion("1.2.3", []);
  const calls = [];
  const env = { ...publishEnv, RELEASE_MODE: "prerelease", EXPECTED_RELEASE_VERSION: version, RELEASE_ASSETS: "dist/*.zip :: The app", RELEASE_VERIFY_COMMAND: "check-it" };
  await publish(env, undefined, scriptedRun(calls), cwd);
  assert.deepEqual(calls.find(call => call[0] === "sh"), ["sh", "-c", "check-it"]);
  assert.ok(calls.findIndex(call => call[0] === "sh") < calls.findIndex(call => call[1] === "release"));
  assert.ok(calls.find(call => call[1] === "release").includes("dist/app.zip#The app"));
});
