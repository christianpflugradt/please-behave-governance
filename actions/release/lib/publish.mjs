import { execFileSync } from "node:child_process";
import { globSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import semanticRelease from "semantic-release";
import { createConfig } from "./config.mjs";
import { removeSeed, seedFirstRelease } from "./first-release.mjs";
import { allReleases, latestStable, settings } from "./plan.mjs";
import { assertNoLocalConfiguration, verifySource } from "./source.mjs";
import { developmentVersion } from "./version.mjs";

const IMAGE_SCRIPT = fileURLToPath(new URL("./release_image.py", import.meta.url));

/** Publishing container images verifies the candidate archives, loads the images before the tag and pushes them after it. */
export function hooksFor(images) {
  if (!images.length) return {};
  const command = operation => `python3 -B "${IMAGE_SCRIPT}" ${operation}`;
  return { verify: command("verify"), prepare: command("load"), publish: command("push") };
}

/**
 * The inline plugin that refuses publication when the release differs from the planned one and runs the
 * repository's optional hooks: `verify` with every verification, `prepare` before the tag is created and
 * `publish` after it. Hooks are shell commands run in the repository root with the release environment.
 */
export function guardFor(expected, env, cwd, run, hooks = {}) {
  const hook = (command, context) => run("sh", ["-c", command], { cwd: context.cwd ?? cwd, env: context.env ?? env, stdio: "inherit" });
  const verifyRelease = (_pluginConfig, context) => {
    if (context.nextRelease.version !== expected) {
      throw new Error("Release version changed after planning; refuse publication.");
    }
    verifySource(context.env ?? env, context.cwd ?? cwd, run);
    if (hooks.verify) hook(hooks.verify, context);
  };
  return {
    verifyRelease,
    ...(hooks.prepare && {
      prepare(pluginConfig, context) {
        hook(hooks.prepare, context);
        // Recheck main after preparing, immediately before Semantic Release tags the source.
        verifyRelease(pluginConfig, context);
      },
    }),
    ...(hooks.publish && { publish: (_pluginConfig, context) => hook(hooks.publish, context) }),
  };
}

export async function publish(env = process.env, release = semanticRelease, run = execFileSync, cwd = process.cwd(), hooks = undefined) {
  const expected = env.EXPECTED_RELEASE_VERSION;
  if (!expected) throw new Error("Planned version is required");
  const { mode, tagFormat, assets, force, repository, images } = settings(env);
  if (images.length && mode !== "release") throw new Error("Container images are published with official releases only");
  hooks ??= hooksFor(images);
  assertNoLocalConfiguration(cwd);
  verifySource(env, cwd, run);
  if (mode === "release") {
    const config = createConfig({ repository, tagFormat, assets, force, guard: guardFor(expected, env, cwd, run, hooks) });
    seedFirstRelease(tagFormat, cwd, env, run);
    let result;
    try {
      result = await release(config, { cwd, env });
    } finally {
      removeSeed(tagFormat, cwd, env, run);
    }
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
