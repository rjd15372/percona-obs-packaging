# Percona OBS Packaging - AI Coding Instructions

## Project Purpose

This repo contains RPM and Debian **packaging metadata** for building Percona software packages via a self-hosted [OpenSUSE Build Service (OBS)](https://build.opensuse.org/) instance. It does **not** contain upstream source code — only packaging files. Sources are fetched at build time by OBS services declared in `obs/_service`.

- `osc` — the OBS CLI client (Python library, also used programmatically)
- `percona-obs` — the management script in this repo (see `requirements.txt`)
- `root/` — all packaging content lives here, mirroring the OBS project/package hierarchy

## Repository Layout

```
root/
├── project.yaml             # OBS project config for the root project
├── <package>/               # top-level package (no subproject)
│   ├── debian/              # Debian packaging (control, rules, changelog, *.install, postinst/prerm hooks)
│   ├── rpm/                 # RPM packaging (*.spec, patches, service files)
│   ├── package.yaml         # optional OBS package config (title, description)
│   └── obs/
│       ├── _service         # OBS build service config — fetches sources and drives the build
│       └── _multibuild      # Multi-flavor build config (PostgreSQL extensions only)
├── <another-package>/       # packages and subprojects can be freely mixed at the root
│   └── ...
└── <subproject>/            # optional grouping (maps to an OBS subproject)
    ├── project.yaml         # OBS project config for this subproject
    ├── <package>/
    │   ├── debian/
    │   ├── rpm/
    │   ├── package.yaml
    │   └── obs/
    │       ├── _service
    │       └── _multibuild
    └── <another-package>/
        └── ...
```

A directory is treated as a **package** if it contains an `obs/` subdirectory or a `package.yaml` file. Everything else is treated as a **project** (subproject grouping).

## Two Package Archetypes

### 1. Standalone service (e.g., `percona-telemetry-agent/`)
- Single static package name (no version placeholder)
- `obs/_service` fetches: packaging (debian + rpm subdirs) + upstream source + `go_modules` (manual)
- `debian/rules` extracts version from `.obsinfo` file at build time
- RPM `Release: 1%{?dist}`

### 2. PostgreSQL extension (e.g., `ppg/17.9/percona-pg-telemetry/`)
- Uses `@BUILD_FLAVOR@` placeholder throughout (replaced by PG major version at build time)
- `obs/_multibuild` lists PG versions to build for: `<flavor>17</flavor>`
- `debian/pgversions` specifies min PG version (e.g., `9.3+`)
- RPM spec defines `%define pg_version @BUILD_FLAVOR@%{nil}` and uses `%{pgrel}` in `Name:`
- Built with PGXS: `USE_PGXS=1 make`

## Critical Conventions

**`obs/_service` structure** (all packages follow this pattern):
1. First `obs_scm` service: fetch `debian/` subdir from this repo
2. Second `obs_scm` service: fetch `rpm/` subdir from this repo
3. Third `obs_scm` service: fetch upstream source from its canonical repo
4. Buildtime services: `tar`, `recompress` (gz), `set_version`
5. `go_modules` (manual mode) — only for Go projects (telemetry-agent, etcd)

**`debian/debian.dsc`** must list all tarballs in `Debtransform-Files-Tar`:
```
Debtransform-Files-Tar: debian.tar.gz vendor.tar.gz rpm.tar.gz
```

**Maintainer** (use consistently):
- `Percona Development Team <info@percona.com>` (Debian)
- `Percona LLC` (RPM)

**Epoch: 1** is set on PostgreSQL-related packages to allow version management.

## Project Configuration (project.yaml)

Each project directory may contain a `project.yaml` that defines its OBS project metadata.

```yaml
name:                          # optional — overrides the OBS project name (empty = use derived name)
title: My Project Title
description: "Human-readable description."
repositories:
  - name: RockyLinux_9         # OBS repository name
    paths:
      - project: openSUSE.org:RockyLinux:9   # upstream OBS project providing the build environment
        repository: standard
      - subproject: builddep   # relative reference: resolves to <rootprj>:builddep
        repository: RockyLinux_9
    archs: [x86_64]
project-config: |              # raw OBS project config string
  %if "%_repository" == "RockyLinux_9"
  ExpandFlags: module:llvm-toolset-rhel9
  %endif
```

- `name` — absent or empty means the OBS project name is derived from the directory path relative to `root/` joined with `--rootprj` using colons (e.g. `home:Admin:ppg:17.9`). Set it explicitly only when the OBS project name must differ from the directory path.
- `repositories[].paths` — list of path entries providing the base build environment. Each entry uses either `project:` (absolute OBS project name) or `subproject:` (resolved as `<rootprj>:<subproject>`) plus `repository:`.
- `project-config` — passed verbatim to the OBS project config API; used for RPM macros, module expansion flags, etc.
- `title` and `description` are informational only and never inherited by child projects.

### Config inheritance

`repositories` and `project-config` are **inherited** from ancestor `project.yaml` files when absent or empty in a project's own file. The nearest ancestor that defines the field wins. `title`, `description`, and `name` are never inherited.

This means:
- The root `project.yaml` acts as the default config for all subprojects.
- A subproject only needs its own `project.yaml` if it requires a different build environment.
- An empty or missing `project.yaml` in a subdirectory is valid — it will fully inherit from its parent.

### Dynamically generated repository paths

When `percona-obs` pushes project metadata to OBS, it automatically injects one `<path>` entry per ancestor OBS project into every repository of every non-root subproject. This is done by `build_project_meta()` in `percona-obs`, using the `_ancestor_projects()` helper.

Ancestor paths are injected closest-first (immediate parent before grandparent), followed by the upstream path from `project.yaml`. This gives every subproject **direct** visibility into packages built in all ancestor projects, without relying on OBS transitive resolution.

For example, the `home:Admin:ppg:17.9` project gets this generated for each repository:
```xml
<repository name="RockyLinux_9">
  <path project="home:Admin:ppg" repository="RockyLinux_9"/>           <!-- auto-injected: immediate parent -->
  <path project="home:Admin" repository="RockyLinux_9"/>               <!-- auto-injected: grandparent (rootprj) -->
  <path project="openSUSE.org:RockyLinux:9" repository="standard"/>   <!-- from project.yaml -->
  <arch>x86_64</arch>
</repository>
```

The root project (matching `--rootprj`) never gets ancestor paths injected. Only non-root subprojects are affected.

## Package Configuration (package.yaml)

Each package directory may contain a `package.yaml` with OBS package metadata:

```yaml
title: My Package Title
description: "Human-readable description."
```

These fields map directly to the OBS package `<title>` and `<description>` XML elements.

## `percona-obs` CLI

`percona-obs` is the management script for syncing local YAML configuration and packaging files to an OBS instance.

**Global options:**
```sh
# Using explicit flags (always works):
percona-obs -A <url> -R <rootprj> [--verbose] <command> ...

# Using a named profile (recommended for day-to-day use):
percona-obs -P <name> [--verbose] <command> ...

#   -A / --apiurl    OBS API URL (e.g. http://my-obs.local:8000)
#   -R / --rootprj   OBS root project (e.g. home:Admin)
#   -P / --profile   Load apiurl + rootprj from .profile/<name>.yaml
#   --verbose        Print debug-level log messages (API calls, unchanged items)
```
`-R` / `--rootprj` is always required — either directly or via a profile.  Explicit `-A` / `-R` flags override the corresponding profile values when both are given.

OBS credentials are read from `~/.config/osc/oscrc` (created by `osc`'s first-run wizard).

### Connection profiles

Profiles store per-environment OBS connection settings in `.profile/<name>.yaml` (git-ignored). Create one file per environment; use `-P <name>` to activate it.

**File format** (`.profile/<name>.yaml`):
```yaml
apiurl: http://192.168.1.103:3000   # OBS API URL
rootprj: home:Admin:percona         # OBS root project
```

**Example** — create a `local` profile and use it:
```sh
mkdir -p .profile
cat > .profile/local.yaml <<EOF
apiurl: http://192.168.1.103:3000
rootprj: home:Admin:percona
EOF

./percona-obs -P local sync ppg:17.9 etcd --dirty --dry-run-remote
```

If the named profile file does not exist, `percona-obs` exits with an error listing the profiles that are available in `.profile/`.

### Output format

`percona-obs` prints one line per resource, always — including unchanged ones. Each line has a two-character prefix, color-coded when stdout is a TTY (set `NO_COLOR=1` to disable):

| Prefix | Color | Meaning |
|---|---|---|
| `  + ` | green | Resource created on OBS (did not exist before) |
| `  ~ ` | yellow | Resource updated on OBS (existed, content changed) |
| `  = ` | dim | Resource unchanged (OBS already matches desired state) |
| `  - ` | red | Resource deleted from OBS (orphan cleanup) |
| `  ! ` | yellow | Uncertain — OBS-only file skipped because services were not run (dry-run only) |
| `  > ` | cyan | Action taken: local service run or OBS service triggered |
| `  ✔ ` | bold green | Command completed successfully |
| `  · ` | dim | Debug message (only shown with `--verbose`) |

In dry-run mode the same `+`/`~`/`=`/`-`/`!` symbols are used — the `(dry run)` note on the final `✔` line indicates nothing was written.

### Git safeguard

`sync` and `config apply` abort if:
- the working tree has uncommitted or untracked changes (`git status --porcelain`), or
- the HEAD commit has not been pushed to any remote (`git branch -r --contains HEAD`).

Use `--dirty` to skip this check (e.g. for local testing or CI pipelines that manage their own state).

### Change detection

Both `sync` and `config apply` compare the desired state against what OBS currently holds **before** making any write call:

- **Project / package meta** — the managed fields (title, description, repositories) are compared as XML; OBS-managed fields (ACL entries, person/group/lock) are ignored.
- **Project config** — the raw string is compared after stripping leading/trailing whitespace.
- **`obs/` files** — each file's MD5 is compared to the MD5 returned by the OBS source directory listing. Only changed files are uploaded. Files present on OBS but absent locally are deleted (real sync) or marked `!` (dry-run, where they may be service-generated artifacts). All uploads and deletions are committed as a single OBS source revision.

Every resource is always printed with its status (`+`/`~`/`=`/`-`/`!`). The `=` line is printed even when nothing changed. Use `--force` to bypass comparison and always write.

### `sync [--force] [--dirty] [--dry-run] [--dry-run-remote] [--no-services] [-m MSG] [project] [package]`

Syncs local packaging files to OBS, creating or updating projects and packages (`obs/_service`, `obs/_multibuild`). For each target package, all ancestor projects (from root down) are created/updated first, then the package meta is applied, then `obs/` source files are synced as a **single OBS source revision** — new and changed files are uploaded, files removed locally are deleted from OBS.

| Call form | Effect |
|---|---|
| `sync` | Sync all packages under `root/` |
| `sync <project>` | Sync all packages under the project (recursively) |
| `sync <top-level-package>` | Sync a single package directly under `root/` |
| `sync <project> <package>` | Sync a single package under the project |

Options:
- `--force` — bypass OBS conflict checks; always write meta and files regardless of diff.
- `--dirty` — skip the git safeguard (allow uncommitted changes or an unpushed HEAD).
- `--dry-run` — make read-only OBS calls to compute what would change, but write nothing. The same `+`/`~`/`=`/`-`/`!` symbols are used. Local services are **not** run; OBS-only files (likely service outputs) are shown with `!` instead of `-`.
- `--dry-run-remote` — run local services for real, then report what would be uploaded to OBS without writing. Use to verify manual services work before committing. All OBS writes are skipped but the `+`/`~`/`=` output reflects what would change.
- `--no-services` — skip local service execution; upload `obs/` as-is even if `_service` declares manual services.
- `-m MSG` / `--message MSG` — commit message recorded in the OBS source revision. When omitted, a message is generated automatically:
  - Normal: `sync: <branch>@<short-sha> (<remote_url>)`
  - With `--dirty`: `sync: <branch>@<short-sha> (local changes on <hostname>)`

### Local service execution

If a package's `obs/_service` contains any service with `mode="manual"`, `sync` automatically runs all non-buildtime services locally before uploading. This is required for packages like Go services that use `go_modules` (mode=manual) to vendor dependencies.

**Execution order and file handling:**
1. All services with `mode` not in `{buildtime, serveronly, disabled}` are run in XML declaration order.
2. Each service binary is invoked from `/usr/lib/obs/service/<name>` with its `<param>` values and `--outdir`.
3. Service outputs are merged into a shared work directory so later services can consume earlier outputs (e.g. `go_modules` consuming `obs_scm` tarballs).
4. Only files produced by `mode="manual"` services are committed to OBS. Files produced by no-mode services (e.g. obs_scm source tarballs) are used locally but **not** uploaded — OBS regenerates those on its server.

If a service binary is missing from `/usr/lib/obs/service/`, a warning is logged and the service is skipped. A non-zero service exit code aborts the entire `sync` run.

#### Local service cache

To avoid re-running expensive operations (git clones, Go dependency vendoring) on every `sync`, `percona-obs` maintains a two-level on-disk cache at `.cache/` in the project root (git-ignored via `.gitignore`).

**Level 1 — obs_scm output cache** (`.cache/obs_scm/{params_hash}/{head_sha}/`)

Before invoking each `obs_scm` service binary, `percona-obs`:
1. Computes `params_hash` as the SHA256 of all sorted `name=value` param pairs from the service XML element. Any change to the service config (URL, revision, extract pattern, etc.) produces a different key.
2. Calls `git ls-remote` (30 s timeout) to resolve the remote revision to a commit SHA (`head_sha`), trying in order: `refs/heads/<revision>`, `refs/tags/<revision>^{}` (annotated tag, peeled to commit), `refs/tags/<revision>`.
3. Checks `.cache/obs_scm/{params_hash}/{head_sha}/`. On a **hit**, all cached files are restored to the work directory and obs_scm is skipped entirely. On a **miss**, obs_scm runs normally and its output files (`.obsinfo`, `.obscpio`, `.dsc`, etc.) are stored atomically to `.cache/obs_scm/{params_hash}/{head_sha}/`.

If `git ls-remote` fails or times out, obs_scm always runs and its output is not stored.

**Level 2 — manual service output cache** (`.cache/services/{upstream_commit}/`)

After Phase 1 completes, `percona-obs` identifies the *upstream source* `obs_scm` service — the one that fetches the actual software being packaged — by filtering out every `obs_scm` whose `subdir` param matches the regex `root/.+/(debian|rpm)$` (those fetch packaging files from this repo). Exactly one service must remain; zero or two or more trigger a warning and the cache is skipped.

The obsinfo file produced by that upstream obs_scm is named `{filename}.obsinfo` (where `filename` is the service's `filename` param, e.g. `etcd.obsinfo`). Its `commit:` field — the HEAD commit of the upstream repo at fetch time — is used as the cache key.

- **Cache hit**: `.cache/services/{upstream_commit}/` exists and contains files → those files (vendor tarballs, etc.) are copied to the work directory, all `mode="manual"` services are skipped, and the function returns immediately.
- **Cache miss**: all `mode="manual"` services run in XML-declaration order, then their output files are stored atomically to `.cache/services/{upstream_commit}/`.

**Atomic writes**: both levels write to a temporary directory inside the cache directory (ensuring same filesystem), then rename it into place, preventing partial or corrupt cache entries.

**`--no-cache`**: pass to `sync` to bypass both cache levels unconditionally for that run.

When targeting a specific package (`sync <project> <package>`), the ancestor project chain is only walked if the target project does not yet exist on OBS (fast path avoids redundant GET calls otherwise).

Project names use colon notation matching the directory hierarchy (e.g. `ppg:17.9`).

### `build trigger [project] [package]`

Triggers an OBS service run (`runservice`) for one or more packages, causing OBS to re-fetch sources and rebuild.

| Call form | Effect |
|---|---|
| `build trigger` | Trigger services for all packages under `root/` |
| `build trigger <project>` | Trigger services for all packages under the project |
| `build trigger <top-level-package>` | Trigger service for a single top-level package |
| `build trigger <project> <package>` | Trigger service for a single package under the project |

### `build status [project] [package]`

Prints a color-coded tree of live build statuses fetched from OBS.

| Call form | Effect |
|---|---|
| `build status` | Status for all packages under `root/` |
| `build status <project>` | Status for all packages under the project (tree rooted there) |
| `build status <top-level-package>` | Status for a single top-level package |
| `build status <project> <package>` | Status for a single package |

Status symbols (color output disabled with `NO_COLOR=1`):

| Symbol | Color | OBS status codes |
|---|---|---|
| `✔` | green | `succeeded` |
| `✗` | red | `failed` / `unresolvable` / `broken` |
| `●` | cyan | `building` / `dispatching` |
| `◌` | yellow | `scheduled` / `blocked` |
| `–` | dim | `excluded` / `disabled` |
| `?` | dim | `unknown` or any unrecognised code |

For multibuild packages, when all flavors of a repository share the same status the flavor tags are shown inline (e.g. `[:17]`). When flavors differ, each expands to its own sub-line under the repository.

When multiple architectures are configured for the same repository, the highest-priority (most actionable) status is kept per flavor; arch details are not shown.

### `config apply [--force] [--dirty] [--dry-run] [project] [package]`

Applies `project.yaml` or `package.yaml` configuration to OBS. Updates project meta (title, description, repositories), project build config, and package meta. Does **not** upload `obs/` source files.

| Call form | Effect |
|---|---|
| `config apply` | Apply `root/project.yaml` to the root project |
| `config apply <project>` | Apply `<project>/project.yaml` to that project |
| `config apply <project> <package>` | Apply `<package>/package.yaml` to that package |

Options:
- `--force` — bypass OBS conflict checks (`?force=1`); use when the server's copy was modified externally.
- `--dirty` — skip the git safeguard.
- `--dry-run` — simulate without writing to OBS.

## Adding a New PostgreSQL Extension
1. Copy `ppg/17.9/percona-pg-telemetry/` as a template
2. Replace all `percona-pg-telemetry` references with the new package name
3. Update `obs/_multibuild` flavors for the target PG versions
4. Update `obs/_service` upstream URL to point to the new package's GitHub repo
5. Update `rpm/*.spec` — preserve `@BUILD_FLAVOR@` in `Name:` and `%define pg_version`
6. Update `debian/control` — keep `@BUILD_FLAVOR@` in `Package:` and version-specific `Depends:`

## Adding a New Standalone Service (Go)
1. Copy `percona-telemetry-agent/` as a template
2. Update `obs/_service` upstream URL; keep `go_modules` service in manual mode
3. `debian/rules` version extraction pattern reads `/usr/src/packages/SOURCES/*.obsinfo`
4. Ensure `vendor.tar.gz` is listed in `debian/debian.dsc`'s `Debtransform-Files-Tar`

## Importing an Existing OBS Package

When given an OBS package URL and a target location within `root/`, follow these steps. The user may request either a **full source import** (copy all files from OBS) or an **aggregate import** (create an `_aggregate` link so the local OBS pulls built packages from the source project). Use the mode explicitly requested; default to full source import if not specified.

### Inputs
- **OBS package URL** — the web UI URL, e.g. `http://192.168.1.103:3000/package/show/home:Admin/obs-service-tar_scm`
- **Target location** — directory relative to `root/` where the package should land (e.g. `root/` for a top-level package, `root/ppg/17.9/` for a subproject package)
- **Import mode** — `full` (copy source files) or `aggregate` (create `_aggregate` link)

### Step 1 — Determine the API URL

The web UI URL and API URL are not always the same host:

| Web UI host | API host to use |
|---|---|
| `build.opensuse.org` | `api.opensuse.org` |
| Any other host | same host as the web UI |

The path format `/package/show/<project>/<package>` always identifies the OBS project and package name regardless of which host is used.

### Step 2 — Fetch package metadata

```sh
osc -A <apiurl> api /source/<obs_project>/<package_name>/_meta
```
Extract `<title>` and `<description>`. Treat a description containing only whitespace as empty.

### Step 3 — Create the directory

```sh
mkdir -p root/<target>/<package_name>/obs
```

### Step 4a — Full source import

List files:
```sh
osc -A <apiurl> api /source/<obs_project>/<package_name>
```
Download each `<entry name="...">`:
```sh
osc -A <apiurl> api /source/<obs_project>/<package_name>/<filename>
```
Place every file directly in `obs/`. Do **not** split into `debian/` or `rpm/` — that is a separate step if desired.

### Step 4b — Aggregate import

Create `obs/_aggregate` pointing to the source project. When the source is on a remote OBS instance, prefix the project name with the instance identifier:

| Source OBS instance | Project name in `_aggregate` |
|---|---|
| `build.opensuse.org` / `api.opensuse.org` | `openSUSE.org:<obs_project>` (e.g. `openSUSE.org:openSUSE:Tools`) |
| Local OBS (`192.168.1.103`) | Use the project name as-is (e.g. `home:Admin`) |

```xml
<aggregatelist>
  <aggregate project="<mapped_project>">
    <package><package_name></package>
  </aggregate>
</aggregatelist>
```

### Step 5 — Write `package.yaml`

```yaml
title: "..."        # quote if the value contains a colon
description: |
  <description from _meta, reflowed to ~80 chars per line>
```
Omit `package.yaml` entirely if both `<title>` and `<description>` are empty.

**Always quote the `title` value with double quotes if it contains a colon (`:`) — YAML treats an unquoted colon as a mapping separator and will fail to parse.**

### Notes
- Use the OBS package name unchanged as the local directory name.
- Do not run `black` or `pyright` — no Python code is modified.
- After creating the files, verify with `find root/<package_name> -type f | sort`.

## Direct OBS CLI (osc)
```sh
# Check out a package from OBS
osc co <project> <package>

# Sync local files into the OBS checkout, then commit
cp -r obs/* <checkout>/
osc add <new-files>
osc ci -m "update _service"

# Trigger a remote rebuild
osc rebuild <project> <package>

# Follow build log
osc buildlog <project> <package> <repo> <arch>
```

## Key Files by Pattern
| Purpose | Exemplar |
|---|---|
| Go standalone package | `percona-telemetry-agent/` |
| PG extension multi-version | `ppg/17.9/percona-pg-telemetry/` |
| Large PG server package | `ppg/17.9/percona-postgresql17/` |
| Third-party infrastructure service | `ppg/17.9/etcd/` |
| OBS aggregate (mirrors another OBS project) | `obs-service-tar_scm/` |
| Root project config | `root/project.yaml` |
| Management script | `percona-obs` (commands: `sync`, `build trigger`, `build status`, `config apply`) |
