# Dependency audit actions

A dependency audit is built from small actions. Each audit action scans one target and records one report file; `report`
judges everything that was recorded. The workflow keeps what differs per repository: its schedule, the tools it
installs, which audit actions it calls and how often, and the severity from which it fails.

| Action | Scans | Needs on `PATH` |
| --- | --- | --- |
| `npm` | `package-lock.json` in `directory` (default `.`) with `npm audit` | `npm` |
| `cargo` | `Cargo.lock` in `directory` (default `.`) with `cargo audit` | `cargo-audit` |
| `python` | the locked versions of the `uv.lock` in `directory` (default `.`) with `pip-audit` | `uv`, `pip-audit` |
| `owasp` | the Dependency-Check JSON `report` that an earlier step produced | nothing |
| `report` | nothing: summary, failure from `fail-on`, upload of the reports | nothing, Python 3 |

```yaml
- uses: christianpflugradt/repository-catalog/actions/dependency-audit/npm@<FULL_COMMIT_SHA>
  with:
    directory: renderer
- uses: christianpflugradt/repository-catalog/actions/dependency-audit/cargo@<FULL_COMMIT_SHA>
  with:
    directory: backend
- uses: christianpflugradt/repository-catalog/actions/dependency-audit/report@<FULL_COMMIT_SHA>
  if: ${{ !cancelled() }}
  with:
    fail-on: medium
```

Call each audit action once per target, and `report` last. The audit actions never fail on findings and never on a scan
that could not complete: they record it, so that later audit steps still run and `report` shows everything at once.
`report` fails when a scan did not complete, when a finding reaches `fail-on` (findings of unknown severity always do)
and when no report was recorded at all, so a repository never passes by scanning nothing.

## The interface

An audit action records `$RUNNER_TEMP/dependency-audit/<name>.json`, one file per scan:

```json
{
  "schema": 1,
  "name": "npm renderer",
  "status": "complete",
  "findings": [{"id": "GHSA-…", "aliases": [], "severity": "high", "description": null, "url": null, "packages": ["pkg 1.0"]}]
}
```

`status` is `complete` or `error` (with an `error` message), `severity` is `low`, `medium`, `high`, `critical` or
`unknown`. A placeholder with status `error` is written before the scan starts, so a scan that dies is never missing and
never clean. A scan that is not covered by an action here is audited by any step that writes such a file; `report` knows
no ecosystem. `report` uploads the directory as the artifact `dependency-audit`, together with `extra-report-paths`.

## Python

`python` audits a locked project: `uv export --locked` turns the `uv.lock` into exactly pinned requirements, and
`pip-audit --no-deps --disable-pip` audits them without resolving anything. A `pyproject.toml` or a `requirements.txt`
without a lock lists ranges and no versions, so a scan of it audits what would be resolved today and not what is used;
it is an error here, and the project is expected to lock with uv.
