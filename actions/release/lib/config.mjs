// The shared Semantic Release policy: commit types, release levels and notes sections.
// Repositories only supply their tag format and release assets.

import { analyzeCommits } from "@semantic-release/commit-analyzer";

// Share revert parsing between version calculation and release notes, including scopes.
const parserOpts = {
  revertPattern: /^revert(?:\([^)]+\))?!?:\s"?([\s\S]+?)"?\s*This reverts commit (\w*)\./i,
  revertCorrespondence: ["header", "hash"],
};

const releaseRules = [
  { breaking: true, release: "major" },
  { type: "feat", release: "minor" },
  ...["fix", "perf", "revert", "chore", "refactor", "config"].map(type => ({ type, release: "patch" })),
  // Ignore historical generated version commits.
  { type: "build", scope: "!release", release: "patch" },
  { type: "build", scope: null, release: "patch" },
  { type: "build", scope: "release", release: false },
  ...["ci", "docs", "style", "test"].map(type => ({ type, release: false })),
];

const noteTypes = [
  { type: "feat", section: "Features" },
  { type: "fix", section: "Bug Fixes" },
  { type: "perf", section: "Performance Improvements" },
  { type: "chore", section: "Maintenance" },
  { type: "build", scope: "release", hidden: true },
  { type: "build", section: "Build System" },
  { type: "refactor", section: "Code Refactoring" },
  { type: "revert", section: "Reverts" },
  { type: "config", section: "Configuration" },
  ...["ci", "docs", "style", "test"].map(type => ({ type, hidden: true })),
];

const analyzerOptions = { preset: "conventionalcommits", parserOpts, releaseRules };

// With `force`, commits that would not release by themselves still yield a patch release; a repository uses this to
// test the release process. While the latest official release is a 0.x version, a breaking change raises the minor
// version, because Semver allows breaking changes in the 0.x series and the jump to 1.0.0 is a deliberate, manual step.
function analyzer({ force }) {
  return {
    async analyzeCommits(pluginConfig, context) {
      let type = await analyzeCommits(pluginConfig, context);
      if (!type && force) type = "patch";
      if (type === "major" && context.lastRelease?.version?.startsWith("0.")) type = "minor";
      return type;
    },
  };
}

/** The commit analyzer and notes generator: all that is needed to plan a release. */
export function analysisPlugins({ force = false } = {}) {
  return [
    [analyzer({ force }), analyzerOptions],
    ["@semantic-release/release-notes-generator", { preset: "conventionalcommits", parserOpts, presetConfig: { types: noteTypes } }],
  ];
}

/**
 * The complete configuration for publication. `guard` is an inline plugin that refuses
 * publication when the planned release no longer matches; it runs between notes and GitHub.
 */
export function createConfig({ repository, tagFormat, assets, guard, force = false }) {
  return {
    repositoryUrl: `https://github.com/${repository}.git`,
    branches: ["main"],
    tagFormat,
    plugins: [
      ...analysisPlugins({ force }),
      ...(guard ? [guard] : []),
      ["@semantic-release/github", { assets, successComment: false, failComment: false, releasedLabels: false }],
    ],
  };
}
