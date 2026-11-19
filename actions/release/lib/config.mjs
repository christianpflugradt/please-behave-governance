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

// With `force`, commits that would not release by themselves still yield a patch release.
// A repository uses this to test the release process, so the shared policy stays untouched otherwise.
const forcingAnalyzer = {
  async analyzeCommits(pluginConfig, context) {
    return (await analyzeCommits(pluginConfig, context)) ?? "patch";
  },
};

/** The commit analyzer and notes generator: all that is needed to plan a release. */
export function analysisPlugins({ force = false } = {}) {
  return [
    force ? [forcingAnalyzer, analyzerOptions] : ["@semantic-release/commit-analyzer", analyzerOptions],
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
