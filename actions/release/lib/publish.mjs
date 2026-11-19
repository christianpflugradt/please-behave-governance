import { execFileSync } from "node:child_process";
import { globSync } from "node:fs";
import { pathToFileURL } from "node:url";
import semanticRelease from "semantic-release";
import { createConfig } from "./config.mjs";
import { allReleases, latestStable, settings } from "./plan.mjs";
import { assertNoLocalConfiguration, verifySource } from "./source.mjs";
import { developmentVersion } from "./version.mjs";

/** Refuses publication when the release differs from the planned one. */
function guardFor(expected, env, cwd, run) {
  return {
    verifyRelease(_pluginConfig, context) {
      if (context.nextRelease.version !== expected) {
        throw new Error("Release version changed after planning; refuse publication.");
      }
      verifySource(context.env ?? env, context.cwd ?? cwd, run);
    },
  };
}

export async function publish(env = process.env, release = semanticRelease, run = execFileSync, cwd = process.cwd()) {
  const expected = env.EXPECTED_RELEASE_VERSION;
  if (!expected) throw new Error("Planned version is required");
  const { mode, tagFormat, assets, force, repository } = settings(env);
  assertNoLocalConfiguration(cwd);
  verifySource(env, cwd, run);
  if (mode === "release") {
    const config = createConfig({ repository, tagFormat, assets, force, guard: guardFor(expected, env, cwd, run) });
    const result = await release(config, { cwd, env });
    if (!result) throw new Error("Planned release disappeared; refuse publication.");
    return;
  }
  const { tag, stable } = latestStable(env, run, repository, tagFormat);
  if (developmentVersion(stable, allReleases(env, run, repository)) !== expected) {
    throw new Error("Development version changed after planning; refuse publication.");
  }
  const files = assets.flatMap(pattern => globSync(pattern, { cwd })).sort();
  if (!files.length) throw new Error("No release assets matched; refuse publication.");
  verifySource(env, cwd, run);
  run("gh", ["release", "create", `dev-${expected}`, ...files, "--repo", repository, "--target", env.GITHUB_SHA,
    "--title", expected, "--notes", `Development prerelease based on official release ${tag}.`, "--prerelease"],
  { cwd, env, stdio: "inherit" });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    await publish();
  } catch (error) {
    console.error(`Release publication failed: ${error.message}`);
    process.exit(1);
  }
}
