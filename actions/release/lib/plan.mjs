import { execFileSync } from "node:child_process";
import { appendFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import semanticRelease from "semantic-release";
import { analysisPlugins, createConfig } from "./config.mjs";
import { assertNoLocalConfiguration, verifySource } from "./source.mjs";
import { developmentVersion, stableFromTag } from "./version.mjs";

export const MODES = ["release", "prerelease"];

/** Reads the action inputs, which the composite action hands over as environment variables. */
export function settings(env) {
  const mode = env.RELEASE_MODE || "release";
  if (!MODES.includes(mode)) throw new Error(`Release mode must be one of ${MODES.join(", ")}`);
  return {
    mode,
    tagFormat: env.RELEASE_TAG_FORMAT || "v${version}",
    assets: (env.RELEASE_ASSETS ?? "").split("\n").map(line => line.trim()).filter(Boolean),
    force: env.RELEASE_FORCE === "true",
    repository: env.GITHUB_REPOSITORY,
  };
}

export function latestStable(env, run, repository, tagFormat) {
  const latest = JSON.parse(run("gh", ["api", `repos/${repository}/releases/latest`], { env, encoding: "utf8" }));
  if (latest.draft || latest.prerelease) throw new Error("Latest official release is not stable");
  return { tag: latest.tag_name, stable: stableFromTag(latest.tag_name, tagFormat) };
}

export function allReleases(env, run, repository) {
  const pages = JSON.parse(run("gh", ["api", "--paginate", "--slurp", `repos/${repository}/releases?per_page=100`], { env, encoding: "utf8" }));
  return pages.flat();
}

/** Plans a release without publishing: no tags, commits or uploads. */
export async function plan(env = process.env, release = semanticRelease, run = execFileSync, cwd = process.cwd()) {
  if (!env.GITHUB_OUTPUT) throw new Error("Release planning runs only in a GitHub Actions workflow");
  const { mode, tagFormat, force, repository } = settings(env);
  assertNoLocalConfiguration(cwd);
  const source = verifySource(env, cwd, run);
  let version = "";
  if (mode === "release") {
    const config = createConfig({ repository, tagFormat, assets: [], force });
    const result = await release({ ...config, dryRun: true, plugins: analysisPlugins({ force }) }, { cwd, env });
    version = result ? result.nextRelease.version : "";
  } else {
    const { stable } = latestStable(env, run, repository, tagFormat);
    version = developmentVersion(stable, allReleases(env, run, repository));
  }
  appendFileSync(env.GITHUB_OUTPUT, `release=${Boolean(version)}\nversion=${version}\nsource_sha=${source}\nmode=${mode}\n`);
  return version;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    await plan();
  } catch (error) {
    console.error(`Release planning failed: ${error.message}`);
    process.exit(1);
  }
}
