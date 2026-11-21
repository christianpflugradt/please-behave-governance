const STABLE = /^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$/;

/** The plain stable version inside a release tag such as `v1.2.3` (tag format `v${version}`) or `1.2.3`. */
export function stableFromTag(tag, tagFormat) {
  const [prefix, suffix, ...rest] = tagFormat.split("${version}");
  if (prefix === undefined || suffix === undefined || rest.length) throw new Error("Tag format must contain ${version} exactly once");
  if (!tag.startsWith(prefix) || !tag.endsWith(suffix)) throw new Error(`Tag ${tag} does not match tag format ${tagFormat}`);
  const stable = tag.slice(prefix.length, tag.length - suffix.length);
  if (!STABLE.test(stable)) throw new Error("Latest official release must have a plain stable version");
  return stable;
}

/** `<stable>-dev.<yyyymmdd>.<n>`, counting up per day in Europe/Berlin across existing `dev-` releases. */
export function developmentVersion(stable, releases, now = new Date()) {
  if (!STABLE.test(stable)) throw new Error("Latest official release must have a plain stable version");
  const date = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/Berlin", year: "numeric", month: "2-digit", day: "2-digit",
  }).format(now).replaceAll("-", "");
  const prefix = `dev-${stable}-dev.${date}.`;
  const sequences = releases.filter(release => release.tag_name.startsWith(prefix))
    .map(release => Number(release.tag_name.slice(prefix.length)))
    .filter(value => Number.isSafeInteger(value) && value > 0);
  return `${stable}-dev.${date}.${Math.max(0, ...sequences) + 1}`;
}
