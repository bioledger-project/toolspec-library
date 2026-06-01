# bioledger-toolspec-library

Curated collection of **tool spec YAMLs** for use with
[`bioledger`](../bioledger). Pull a family or a single command into your
local BioLedger install to make those tools available without writing
specs from scratch.

## Status

**Alpha.** Phase 1 ships five tool families (`bwa-mem2`, `samtools`,
`gatk`, `bcftools`, `rtg-tools`) covering reference prep, alignment,
variant calling, VCF ops, and benchmarking. More families are added as
they're written and validated.

## Relationship to other repos

```
bioledger-toolspec-schema   ← defines the YAML format
        │
        ▼
bioledger-toolspec-library  ← THIS REPO: instances of that format
        │
        ▼
bioledger                   ← consumes specs at runtime
```

- The schema (pydantic models + validator) lives in
  [`bioledger-toolspec-schema`](../bioledger-toolspec-schema). This repo's
  CI imports it to validate every spec.
- `bioledger` itself depends on `bioledger-toolspec-schema` and can pull
  specs from this repo at install/sync time.

CI installs `bioledger-toolspec-schema` from its own repo
(`bioledger-project/toolspec-schema`, checked out into `.schema`) and
imports `bioledger_toolspec_schema` to validate every committed spec.

## Directory layout

```
bioledger-toolspec-library/
├── README.md
├── pyproject.toml                       # dev/test deps only
├── specs/                               # top level: tool families
│   ├── hyphy/
│   │   ├── family.yaml                  # optional family metadata
│   │   ├── absrel/
│   │   │   ├── spec.yaml                # ExecutionSpec + optional inline interface
│   │   │   ├── tests.yaml               # declarative test cases
│   │   │   └── fixtures/                # tiny test inputs (optional)
│   │   ├── busted/
│   │   │   ├── spec.yaml
│   │   │   └── tests.yaml
│   │   └── ...
│   ├── samtools/
│   │   ├── family.yaml
│   │   ├── sort/{spec.yaml,tests.yaml}
│   │   └── index/{spec.yaml,tests.yaml}
│   └── fastqc/                          # single-command "family"
│       └── fastqc/
│           ├── spec.yaml
│           └── tests.yaml
├── conftest.py                          # path-addressable collector (repo root)
└── .github/workflows/ci.yml
```

### Conventions

- **Tools live in families.** A family is a related set of commands sharing
  a tool brand (e.g. `hyphy`, `samtools`, `fastqc`). Even single-command
  tools get a family directory. Consistency > brevity.
- **One command per directory.** The directory name MUST match
  `spec.execution.name`. CI enforces this.
- **Spec, tests, and fixtures are co-located** in the command directory.
- **UI metadata lives inline in `spec.yaml`** under the optional top-level
  `interface:` key. The schema (`ToolSpec`) supports it natively; we do
  not maintain a separate `interface.yaml`. Omit the block entirely if
  the tool has no UI hints.
- **`family.yaml` is optional** and holds shared metadata (homepage,
  citation, license, default container base). It is *not* validated as a
  `ToolSpec`; it has its own light schema (TBD when needed).
- **No large fixture files committed directly.** If a test needs more than
  a few KB of input, use git-lfs or a download manifest (decision pending
  on first need).

## Authoring a tool spec

The authoritative format reference is
[`bioledger/src/bioledger/toolspec/README.md`](../bioledger/src/bioledger/toolspec/README.md)
(it will move to `bioledger-toolspec-schema` once extracted). A minimal
example:

```yaml
# specs/samtools/sort/spec.yaml
spec_version: "0.1"
execution:
  name: sort                         # MUST equal the directory name
  version: "1.17"
  description: Sort a BAM file by coordinate
  container: quay.io/biocontainers/samtools:1.17--hd87286a_0
  command: >-
    samtools sort -@ {{parameters.threads}}
    -o {{outputs._dir}}/sorted.bam
    {{inputs.input_bam}}
  inputs:
    input_bam:
      type: file
      format: bam
      required: true
  outputs:
    sorted:
      type: file
      format: bam
      pattern: sorted.bam
  parameters:
    threads:
      type: integer
      default: 4
      min: 1
      max: 64
  categories: [alignment, preprocessing]
```

## Testing framework

Tests run with `pytest` and are designed so that **changing a tool always
tests that tool, automatically** — no markers, labels, or schedules to
remember.

### Two layers, both always-on per command

**Layer A — schema/lint** (fast, deps: `pyyaml` + `pydantic` + the schema
package). For every `specs/<family>/<command>/spec.yaml`:

1. `load_spec(path)` succeeds (syntactic + pydantic validation).
2. `validate_spec(spec)` reports zero `ERROR`s.
3. The directory name equals `spec.execution.name`.
4. (Soft) `validate_spec(spec, strict=True)` — warnings reported but not
   failing initially; we tighten over time.

**Layer B — behavioral** (deps: Docker/Podman if a case opts into
container execution). For each case in `tests.yaml`:

1. **Render check** — render the Jinja `command` template with the case's
   `inputs` + `parameters` and assert on `command_contains` / exact match.
   Always runs.
2. **Container run** — if the case declares `run: true` (default `false`),
   actually invoke the container in a tmp workdir, then assert each
   declared output exists per its `pattern` and matches any `sha256` /
   `min_size` checks.

```yaml
# specs/samtools/sort/tests.yaml
cases:
  - name: basic_sort
    inputs:
      input_bam: fixtures/tiny.unsorted.bam   # relative to command dir
    parameters:
      threads: 2
    expects:
      command_contains:
        - "samtools sort"
        - "-@ 2"
    run: true                                 # opt into container execution
    outputs:
      sorted:
        exists: true
        min_size: 100
```

### Graceful skips (no false failures)

A Layer B case **skips with a clear reason** rather than failing when:

- a referenced fixture file is missing (e.g. LFS not yet wired up);
- `run: true` but Docker/Podman is not available on the runner;
- the container image cannot be pulled.

This lets us keep "always run behavioral tests for changed tools" honest
without blocking PRs that legitimately ship a spec ahead of fixtures.

### Targeted runs (CLI)

A `conftest.py` collector at the repo root makes every command
directory addressable as a pytest path, so any of these work:

```bash
pytest                                  # all commands, all layers
pytest specs/hyphy/                     # whole family
pytest specs/hyphy/absrel/              # one command (both layers)
pytest specs/hyphy/absrel/ -k render    # narrow within a command
```

Under the hood the collector walks `specs/`, finds each command dir, and
emits one parametrized test per case (plus the four lint checks per
command). Pytest's normal path-based selection then "just works".

### CI: changed-only by default, full sweep on main

`.github/workflows/ci.yml` does this on PRs:

1. `git diff --name-only origin/<base>...HEAD` to find changed paths.
2. Map paths to a set of touched command dirs. Rules:
   - `specs/<family>/<command>/**` → that command.
   - changes to `conftest.py`, `pyproject.toml`, or the workflow itself →
     run **everything**.
3. `pytest` with that subset of dirs as positional args. Both Layer A
   and Layer B run; container cases either execute or skip per the rules
   above.

On pushes to `main` and on a nightly schedule, the workflow runs the full
suite as a safety net so regressions in unchanged tools (e.g. an upstream
container disappearing) are caught quickly.

No PR labels, no `-m container`, no manual opt-in: when you change a
tool's spec, that tool's behavior gets tested.

## Local development

```bash
# from this repo's root
pip install -e .                                  # installs test deps
pip install -e ../bioledger-toolspec-schema       # editable schema (or ../bioledger pre-extraction)
pytest                                            # full sweep
pytest specs/hyphy/absrel/                        # just one command
```

## Open questions / TODOs

- [ ] Add container execution to behavioral tests (`run: true`) once
      the first Docker-backed command is ready.
- [ ] Ship a second family to stress-test the `family.yaml` schema.
- [ ] Automate manifest URL health checks on the nightly CI schedule.
