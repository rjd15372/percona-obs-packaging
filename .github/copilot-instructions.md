# Percona OBS Packaging - AI Coding Instructions

## Project Purpose
This repo contains packaging metadata (Debian and RPM) for building Percona packages via [OpenSUSE Build Service (OBS)](https://build.opensuse.org/). It does **not** contain upstream source code — it only contains packaging files. `osc` is the OBS CLI client and `percona-obs` is the management script (`requirements.txt`).

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

All packages metadata is stored under the root directory, with optional subproject grouping. This follows OBS's filesystem layout conventions.

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

Each project directory may contain a `project.yaml` that defines its OBS project metadata. See `root/project.yaml` for a full example.

```yaml
name:                          # optional — overrides the OBS project name (empty = use derived name)
title: My Project Title
description: "Human-readable description."
repositories:
  - name: RockyLinux_9         # OBS repository name
    path:
      project: openSUSE.org:RockyLinux:9   # upstream mirror project
      repository: standard
    archs: [x86_64]
project-config: |              # raw OBS project config string
  %if "%_repository" == "RockyLinux_9"
  ExpandFlags: module:llvm-toolset-rhel9
  %endif
```

- `name` being absent or empty means the OBS project name is derived from the directory path + `--rootprj` (e.g. `home:Admin:ppg:17.9`)
- `repositories[].path` points to an existing OBS project/repo to use as the build host
- `project-config` is passed verbatim to the OBS project config API

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
percona-obs -A <url> -R <rootprj> <command> ...
#   -A / --apiurl    OBS API URL (e.g. http://my-obs.local:8000)
#   -R / --rootprj   OBS root project (e.g. home:Admin)
```
OBS credentials are read from `~/.config/osc/oscrc` (created by `osc`'s first-run wizard).

### `deploy [project] [package]`

Creates or updates an OBS project/package from the local packaging files (`obs/_service`, `obs/_multibuild`).

| Call form | Effect |
|---|---|
| `deploy` | Deploy all packages under `root/` |
| `deploy <project>` | Deploy all packages under the project (recursively) |
| `deploy <top-level-package>` | Deploy a single package directly under `root/` |
| `deploy <project> <package>` | Deploy a single package under the project |

Project names use colon notation matching the directory hierarchy (e.g. `ppg:17.9`).

### `config apply [--force] [project] [package]`

Applies `project.yaml` or `package.yaml` configuration to OBS. Updates project meta (title, description, repositories), project build config, and package meta.

| Call form | Effect |
|---|---|
| `config apply` | Apply `root/project.yaml` to the root project |
| `config apply <project>` | Apply `<project>/project.yaml` to that project |
| `config apply <project> <package>` | Apply `<package>/package.yaml` to that package |

`--force` bypasses OBS conflict checks (`?force=1`) — use when the server's copy was modified externally.

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
| Root project config | `root/project.yaml` |
| Management script | `percona-obs` |
