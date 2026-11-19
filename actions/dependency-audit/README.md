# Dependency audit action

One step for the Dependency Audit workflow: it scans, renders the findings as a step summary, fails from a severity and
uploads the report as the artifact `dependency-audit`. The workflow keeps what differs per repository: its schedule, the
audit tools it installs (`npm`, `cargo-audit`, `pip-audit`), further preparation such as a Dependency-Check run, and
`AUDIT_FAIL_ON`.

```yaml
- name: Audit the dependencies
  uses: christianpflugradt/please-behave-governance/actions/dependency-audit@<FULL_COMMIT_SHA>
  with:
    npm: |
      .
      renderer
    cargo: backend
    fail-on: ${{ env.AUDIT_FAIL_ON }}
```

| Input | Scans |
| --- | --- |
| `npm`, `cargo` | the given directories with `npm audit` or `cargo audit` |
| `requirements`, `pyproject` | pip requirements files, or the dependencies of a `pyproject.toml`, with `pip-audit` |
| `owasp-report` | a Dependency-Check JSON report that an earlier step produced |
| `none` | nothing: states that there is nothing to audit and records an empty report |

Without any input and without `none`, the step fails, so that a repository never passes by scanning nothing. A scan that
cannot complete is an error, never an empty result. `extra-report-paths` uploads further files with the report.
