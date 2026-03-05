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
    path:
      project: openSUSE.org:RockyLinux:9   # upstream OBS project providing the build environment
      repository: standard
    archs: [x86_64]
project-config: |              # raw OBS project config string
  %if "%_repository" == "RockyLinux_9"
  ExpandFlags: module:llvm-toolset-rhel9
  %endif
```

- `name` — absent or empty means the OBS project name is derived from the directory path relative to `root/` joined with `--rootprj` using colons (e.g. `home:Admin:ppg:17.9`). Set it explicitly only when the OBS project name must differ from the directory path.
- `repositories[].path` — points to an existing OBS project/repo that provides the base build environment (OS packages, toolchain).
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

**Global options** (required on every call):
```sh
percona-obs -A <url> -R <rootprj> [--verbose] <command> ...
#   -A / --apiurl    OBS API URL (e.g. http://my-obs.local:8000)
#   -R / --rootprj   OBS root project (e.g. home:Admin)
#   --verbose        Print debug-level log messages (API calls, unchanged items)
```
OBS credentials are read from `~/.config/osc/oscrc` (created by `osc`'s first-run wizard).

### Output format

`percona-obs` prints only the actions that produce an actual change. Each line has a two-character prefix, color-coded when stdout is a TTY (set `NO_COLOR=1` to disable):

| Prefix | Color | Meaning |
|---|---|---|
| `  + ` | green | Change applied to OBS |
| `  ~ ` | yellow | Would change (dry-run mode only — nothing written) |
| `  > ` | cyan | Service triggered |
| `  ✔ ` | bold green | Command completed successfully |
| `  · ` | dim | Debug message (only shown with `--verbose`) |

### Git safeguard

`sync` and `config apply` abort if:
- the working tree has uncommitted or untracked changes (`git status --porcelain`), or
- the HEAD commit has not been pushed to any remote (`git branch -r --contains HEAD`).

Use `--dirty` to skip this check (e.g. for local testing or CI pipelines that manage their own state).

### Change detection

Both `sync` and `config apply` compare the desired state against what OBS currently holds **before** making any write call:

- **Project / package meta** — the managed fields (title, description, repositories) are compared as XML; OBS-managed fields (ACL entries, person/group/lock) are ignored.
- **Project config** — the raw string is compared after stripping leading/trailing whitespace.
- **`obs/` files** — each file's MD5 is compared to the MD5 returned by the OBS source directory listing. Only changed files are uploaded.

If nothing changed, no API write call is made and no output line is printed for that item. Use `--force` to bypass comparison and always write.

### `sync [--force] [--dirty] [--dry-run] [-m MSG] [project] [package]`

Syncs local packaging files to OBS, creating or updating projects and packages (`obs/_service`, `obs/_multibuild`). For each target package, all ancestor projects (from root down) are created/updated first, then the package meta is applied, then `obs/` source files are uploaded as a **single OBS source revision**.

| Call form | Effect |
|---|---|
| `sync` | Sync all packages under `root/` |
| `sync <project>` | Sync all packages under the project (recursively) |
| `sync <top-level-package>` | Sync a single package directly under `root/` |
| `sync <project> <package>` | Sync a single package under the project |

Options:
- `--force` — bypass OBS conflict checks; always write meta and files regardless of diff.
- `--dirty` — skip the git safeguard (allow uncommitted changes or an unpushed HEAD).
- `--dry-run` — make read-only OBS calls to compute what would change, but write nothing. Outputs `  ~ ` lines instead of `  + `.
- `-m MSG` / `--message MSG` — commit message recorded in the OBS source revision. When omitted, a message is generated automatically:
  - Normal: `sync: <branch>@<short-sha> (<remote_url>)`
  - With `--dirty`: `sync: <branch>@<short-sha> (local changes on <hostname>)`

When targeting a specific package (`sync <project> <package>`), the ancestor project chain is only walked if the target project does not yet exist on OBS (fast path avoids redundant GET calls otherwise).

Project names use colon notation matching the directory hierarchy (e.g. `ppg:17.9`).

### `build [project] [package]`

Triggers an OBS service run (`runservice`) for one or more packages, causing OBS to re-fetch sources and rebuild.

| Call form | Effect |
|---|---|
| `build` | Trigger services for all packages under `root/` |
| `build <project>` | Trigger services for all packages under the project |
| `build <top-level-package>` | Trigger service for a single top-level package |
| `build <project> <package>` | Trigger service for a single package under the project |

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

When given an OBS package URL and a target location within `root/`, follow these exact steps:

### Inputs
- **OBS package URL** — the web UI URL, e.g. `http://192.168.1.103:3000/package/show/home:Admin/obs-service-tar_scm`
- **Target location** — directory relative to `root/` where the package should land (e.g. `root/` for a top-level package, `root/ppg/17.9/` for a subproject package)

### Steps

**1. Parse the URL**
Extract from the URL:
- `apiurl`: the scheme+host+port (`http://192.168.1.103:3000`)
- `obs_project`: the project name (`home:Admin`)
- `package_name`: the package name (`obs-service-tar_scm`) — this becomes the local directory name

**2. Fetch package metadata**
```sh
osc -A <apiurl> api /source/<obs_project>/<package_name>/_meta
```
Extract `<title>` and `<description>` from the returned XML. These populate `package.yaml`.

**3. List package source files**
```sh
osc -A <apiurl> api /source/<obs_project>/<package_name>
```
The returned XML `<directory>` lists all `<entry name="...">` elements. Collect all file names.

**4. Download each file**
For each file name from step 3:
```sh
osc -A <apiurl> api /source/<obs_project>/<package_name>/<filename>
```

**5. Create the directory structure**
```
root/<target>/<package_name>/
├── package.yaml       ← title + description from _meta (omit if both are empty)
└── obs/               ← all files downloaded in step 4
    ├── <file1>
    └── <file2>
```
All OBS source files go into `obs/` regardless of type (`_aggregate`, `_service`, `_multibuild`, `*.spec`, `*.tar.gz`, etc.). Do **not** split them into `debian/` or `rpm/` subdirs during import — that restructuring is a separate step if desired.

**6. Write `package.yaml`**
```yaml
title: <title from _meta>
description: |
  <description from _meta, reflowed to ~80 chars per line>
```
Omit `package.yaml` entirely if both `<title>` and `<description>` are empty in `_meta`.

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
| Management script | `percona-obs` (commands: `sync`, `build`, `config apply`) |
