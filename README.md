# percona-obs-packaging

RPM and Debian **packaging metadata** for building Percona software packages against an
[OpenSUSE Build Service (OBS)](https://build.opensuse.org/) instance — the public
instance at `build.opensuse.org` or any private deployment.

This repository does **not** contain upstream source code. It contains only the
packaging files (`debian/`, `rpm/`, `obs/_service`, etc.). Sources are fetched at
build time by the OBS services declared in each package's `obs/_service` file.

All package metadata, OBS service configs, and subproject definitions are stored under
the `root/` directory, which mirrors the OBS project and package hierarchy.

The `percona-obs` script in this repository is the management tool for syncing the
local `root/` tree to an OBS instance.

---

## Requirements

### System packages

The following OBS service binaries must be installed on the machine running `percona-obs`.
They are invoked locally for packages that declare `mode="manual"` services (e.g. Go
dependency vendoring):

| Binary | Package (Debian/Ubuntu) | Package (RPM) |
|---|---|---|
| `obs_scm` | `obs-service-obs-scm` | `obs-service-obs_scm` |
| `go_modules` | `obs-service-go_modules` | `obs-service-go_modules` |
| `download_url` | `obs-service-download_url` | `obs-service-download_url` |

Binaries are expected at `/usr/lib/obs/service/<name>`.

> Services that are not installed are skipped with a warning. Only `mode="manual"`
> service outputs (e.g. `vendor.tar.gz`) need to be produced locally — all other
> services run server-side on OBS.

### Python environment

Python 3.8+ is required. Create a virtualenv and install dependencies:

```sh
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

### OBS credentials

Credentials are read from `~/.config/osc/oscrc`. Run the `osc` first-run wizard to
create the file:

```sh
osc -A http://<your-obs-host>:8000 list
```

Follow the prompts to enter your username and password. The file is created once and
reused by all subsequent `osc` and `percona-obs` invocations.

---

## Repository layout

All packaging content lives under `root/`, whose directory structure mirrors the OBS
project and package hierarchy:

```
root/
├── project.yaml                # OBS root project config (repos, build config)
├── <package>/                  # top-level package (no subproject)
│   ├── debian/                 # Debian packaging files
│   ├── rpm/                    # RPM spec and patches
│   ├── package.yaml            # optional OBS package metadata (title, description)
│   └── obs/
│       ├── _service            # OBS build service config
│       └── _multibuild         # multi-flavor build (PostgreSQL extensions only)
└── <subproject>/               # subproject grouping (maps to an OBS subproject)
    ├── project.yaml            # subproject OBS config (inherits from root if absent)
    └── <package>/
        └── ...
```

A directory is a **package** if it contains an `obs/` subdirectory or a `package.yaml`
file. Everything else is treated as a **subproject**.

---

## Usage

Every `percona-obs` command needs to know the OBS API URL (`-A`) and the root
project (`-R`). The recommended way to avoid typing these on every invocation is
to create a **connection profile** once and then use `-P <name>` to activate it.

### Connection profiles

A profile stores `apiurl` and `rootprj` in `.profile/<name>.yaml` (git-ignored).
Create one with the `profile create` command, passing `-A` and `-R` explicitly:

```sh
./percona-obs -A http://my-obs.local:8000 -R home:Admin profile create local
#   + local  (.profile/local.yaml)
#   ✔  profile create: local
```

Running the same command again with different values overwrites the profile
(shown with `~` instead of `+`).

List all available profiles and their settings:

```sh
./percona-obs profile list
#   local
#     apiurl:   http://my-obs.local:8000
#     rootprj:  home:Admin
```

Once a profile exists, activate it with `-P`:

```sh
./percona-obs -P local sync ppg:17.9 etcd --dirty --dry-run-remote
```

Explicit `-A`/`-R` flags always override the profile values when both are given.

---

## Examples

### Preview all changes without writing to OBS

```sh
./percona-obs -P local sync push --dry-run
```

Shows everything that would be created or updated. Nothing is written to OBS.

### Preview changes and also run local services (Go vendoring, etc.)

```sh
./percona-obs -P local sync push --dry-run-remote
```

Same as `--dry-run` but also runs `mode="manual"` services locally, letting you verify
that tools like `go_modules` work correctly before committing anything to OBS.

### Sync all packages

```sh
./percona-obs -P local sync push
```

Walks the entire `root/` tree, creates or updates all OBS projects and packages, and
uploads any changed `obs/` files as a single revision per package.

### Sync a single standalone package

```sh
./percona-obs -P local sync push percona-telemetry-agent
```

### Sync a single PostgreSQL extension

PG extensions live under a subproject (`ppg/17.9/`). Pass the subproject and package
name separately:

```sh
./percona-obs -P local sync push ppg:17.9 percona-pg-telemetry
```

### Sync all packages under a subproject

```sh
./percona-obs -P local sync push ppg:17.9
```

---

## Branching from an existing profile

### What it does

`--branch-from <profile>` speeds up syncing a new environment by reusing already-built
binaries from an existing OBS project instead of re-uploading sources and waiting for
every package to build again from scratch.

For each package that is **unchanged** since the branch profile's last sync, `percona-obs`
uploads only a small `_aggregate` file. OBS then pulls the pre-built binaries directly
from the branch project's repository — no source fetch, no compilation, no wait.
Only packages that have **actually changed** are uploaded with their full source files
and built fresh.

### Typical workflow

Suppose you maintain a stable production profile `prod` (`home:Admin:percona`) and want
to spin up a test environment (`home:Admin:percona-test`) that tracks a feature branch.
Most packages are identical; only one or two have been modified.

**Step 1 — Create a profile for the new environment:**

```sh
./percona-obs -A http://my-obs.local:8000 -R home:Admin:percona-test profile create test
```

**Step 2 — Sync the test environment, branching from prod:**

```sh
./percona-obs -P test sync push --branch-from prod
```

For every unchanged package, `percona-obs` uploads an `_aggregate` pointing at
`home:Admin:percona` — the prod project — and OBS serves the binaries from there
instantly. Modified packages get their sources uploaded and build normally.

Output example:

```
  + project meta  home:Admin:percona-test
  + project meta  home:Admin:percona-test:ppg
  + project meta  home:Admin:percona-test:ppg:17.9
  = files  home:Admin:percona-test:ppg:17.9/percona-postgresql17
  @ home:Admin:percona-test:ppg:17.9/percona-postgresql17  → home:Admin:percona:ppg:17.9/percona-postgresql17
  ~ 4 files  home:Admin:percona-test:ppg:17.9/percona-pg-telemetry   ← changed, uploaded
  ✔  sync successful
```

### Promoting branch packages to full sources

After branching, when you want a package (or all packages) to build from local sources instead
of pulling pre-built binaries from the branch project, run `sync promote`:

```sh
./percona-obs -P test sync promote           # promote all branch packages
./percona-obs -P test sync promote ppg:17.9  # promote all packages under a subproject
./percona-obs -P test sync promote ppg:17.9 etcd  # promote a single package
```

For each package whose latest OBS revision was created by a `--branch-from` sync,
`percona-obs` replaces the `_aggregate` with the full local `obs/` source files
(running any `mode="manual"` services as needed).  Packages that already hold
real sources are skipped with `=`.

Preview what would be promoted without writing to OBS:

```sh
./percona-obs -P test sync promote --dry-run
```

### Build dependency propagation

When branching, packages that depend on a changed package must also be rebuilt from
source — otherwise they might link against stale binaries from the branch project.
`percona-obs` handles this automatically.

After the initial changed/unchanged classification, `percona-obs` queries OBS
`_builddepinfo` for the branch project to determine which packages build-depend on
which others. It then applies **bidirectional dep propagation**:

- If package **A** is promoted (uploaded with sources), every package that **depends
  on A** (directly or transitively) is also promoted.
- Conversely, every package that **A depends on** is also promoted, so A builds
  against fresh locally-controlled binaries rather than the branch copy.

This fixed-point iteration continues until no more promotions are triggered. The
result is a minimal set of packages that must be built from source, with all others
remaining as lightweight aggregates.

**Example**: if `golang-1.25` has changed locally, `percona-telemetry-agent` and
`etcd` (which both build-depend on it) are automatically promoted even if their own
source files are identical to what was last synced to the branch project.

Use `build dependency` to inspect these relationships before syncing:

```sh
./percona-obs -P local build dependency
```

### How unchanged packages are detected

`percona-obs` uses a two-level decision for each package:

1. **Fast path** — reads the last OBS revision comment on the branch project. If it
   contains a clean `sync: <branch>@<sha> (...)` message, `git log` checks whether any
   local commits touch that package since that SHA. No commits → aggregate. Commits → upload.

2. **Content check fallback** — used when the revision message is absent, in a different
   format, or was written with `--dirty`. Compares MD5s of every local `obs/` file against
   what OBS holds, and also verifies that the upstream source commit hash in the `.obsinfo`
   file matches the current remote HEAD via `git ls-remote`. Both must match → aggregate.

---

## Triggering and monitoring builds

### Trigger a rebuild

```sh
./percona-obs -P local build trigger                        # all packages
./percona-obs -P local build trigger ppg:17.9              # all packages under a subproject
./percona-obs -P local build trigger ppg:17.9 etcd         # single package
```

Sends an OBS service run request (`runservice`) for each targeted package, causing
OBS to re-fetch sources and queue a new build.

### Check build status

```sh
./percona-obs -P local build status
```

Prints a color-coded tree of live build statuses fetched from OBS. Succeeded packages
display the built version next to the status:

```
home:Admin:percona
├── percona-telemetry-agent
│   ├── RockyLinux_9           ✔ succeeded     3.5.26-6.1
│   ├── Debian_13              ✔ succeeded     3.5.26-6.1
│   └── xUbuntu_24.04          ✔ succeeded     3.5.26-6.1
├── builddep
│   ├── golang-1.25
│   │   ├── RockyLinux_9       ✔ succeeded
│   │   ├── Debian_13          ✔ succeeded
│   │   └── xUbuntu_24.04      ✔ succeeded
│   └── obs-service-tar_scm
│       ├── RockyLinux_9       ✔ succeeded
│       ├── Debian_13          ✗ failed
│       └── xUbuntu_24.04      ✔ succeeded
└── ppg
    └── 17.9
        ├── etcd
        │   ├── RockyLinux_9   ✔ succeeded     3.5.26-6.1
        │   ├── Debian_13      ✔ succeeded     3.5.26-6.1
        │   └── xUbuntu_24.04  ✔ succeeded     3.5.26-6.1
        └── percona-pg-telemetry:17
            ├── RockyLinux_9   ✔ succeeded     1.0.0-1.1
            ├── Debian_13      ✔ succeeded     1.0.0-1.1
            └── xUbuntu_24.04  ◌ scheduled
```

| Symbol | Color | Meaning |
|---|---|---|
| `✔` | green | `succeeded` |
| `✗` | red | `failed` / `unresolvable` / `broken` |
| `●` | cyan | `building` / `dispatching` |
| `◌` | yellow | `scheduled` / `blocked` |
| `–` | dim | `excluded` / `disabled` |

Scope can be narrowed the same way as other commands:

```sh
./percona-obs -P local build status ppg:17.9               # subproject only (tree rooted there)
./percona-obs -P local build status ppg:17.9 etcd          # single package
./percona-obs -P local build status --repo RockyLinux_9    # all packages, one distro only
```

Set `NO_COLOR=1` to disable color output.

### Show build dependency tree

```sh
./percona-obs -P local build dependency
```

Queries OBS `_builddepinfo` for all packages and prints a dependency tree grouped by
**root packages** — packages that no other local package depends on. Each root package
is shown with its direct and transitive build dependencies indented beneath it.
Packages in the tree are annotated with the OBS project they belong to.

```
etcd (home:Admin:percona:ppg:17.9)
└── golang-1.25 (home:Admin:percona:builddep)

percona-pg-telemetry (home:Admin:percona:ppg:17.9)
├── percona-postgresql-common (home:Admin:percona:ppg:17.9)
└── percona-postgresql17 (home:Admin:percona:ppg:17.9)

percona-telemetry-agent (home:Admin:percona)
└── golang-1.25 (home:Admin:percona:builddep)

obs-service-recompress (home:Admin:percona:builddep)
obs-service-set_version (home:Admin:percona:builddep)
obs-service-tar_scm (home:Admin:percona:builddep)
```

Packages with no local build dependencies and that nothing else depends on are listed
at the bottom as isolated packages. Scope can be narrowed to a subproject:

```sh
./percona-obs -P local build dependency ppg:17.9
```

---

## Getting repository installation instructions

`project install` prints the shell commands needed to configure the OBS-hosted package
repositories on a target machine, grouped by distribution.

> This command contacts the OBS instance to resolve the download URL, so it requires a
> profile (or explicit `-A`/`-R`).

### Show instructions for all distributions

```sh
./percona-obs -P local project install
```

### Show instructions for a specific subproject

```sh
./percona-obs -P local project install ppg:17.9
```

### Filter to a single distribution

```sh
./percona-obs -P local project install --repo RockyLinux_9
```

Example output for a Rocky Linux 9 repository:

```
────────────────────────────────────────────────────────────────────────
RockyLinux_9

# home:Admin:percona:ppg:17.9
rpm --import http://my-obs.local/home:/Admin:/percona:/ppg:/17.9/RockyLinux_9/repodata/repomd.xml.key
tee /etc/yum.repos.d/home_Admin_percona_ppg_17_9.repo << 'EOF'
[home:Admin:percona:ppg:17.9]
name=home:Admin:percona:ppg:17.9 - RockyLinux_9
baseurl=http://my-obs.local/home:/Admin:/percona:/ppg:/17.9/RockyLinux_9/
enabled=1
gpgcheck=0
EOF

```

For Debian-based distributions, instructions use `echo … | tee` + `curl … | gpg --dearmor | tee`
followed by `apt update`. For openSUSE/SLE repositories, `zypper addrepo` +
`zypper --gpg-auto-import-keys refresh` is emitted instead.

Projects that set `install: false` in their `project.yaml`, or that contain no packages,
are silently excluded from the output.

---

## Deleting a project from OBS

### Preview what would be deleted

```sh
./percona-obs -P local sync delete --dry-run
./percona-obs -P local sync delete ppg:17.9 --dry-run
```

### Delete a full project tree

```sh
./percona-obs -P local sync delete --yes --recursive
```

Deletes the root project and all sub-projects (deepest first). Prompts for confirmation
unless `--yes` is given. Use `--recursive` to delete projects that still contain packages.

### Delete a single subproject

```sh
./percona-obs -P local sync delete ppg:17.9 --yes --recursive
```

### Delete a single package

```sh
./percona-obs -P local sync delete ppg:17.9 etcd --yes
```

---

## Adding a new package

### Standalone service (Go or other)

1. Copy an existing standalone package as a template:
   ```sh
   cp -r root/percona-telemetry-agent root/my-new-service
   ```
2. Edit `obs/_service` — update the upstream source URL and any service parameters.
3. Edit `rpm/*.spec` and `debian/control`, `debian/changelog` with the new package name
   and version.
4. Optionally create `package.yaml` with a title and description.
5. Sync to OBS:
   ```sh
   ./percona-obs -P local sync push my-new-service
   ```

### PostgreSQL extension

1. Copy an existing PG extension as a template:
   ```sh
   cp -r root/ppg/17.9/percona-pg-telemetry root/ppg/17.9/my-pg-extension
   ```
2. Replace all `percona-pg-telemetry` references with `my-pg-extension` throughout the
   copied files.
3. Update `obs/_service` to point to the new package's upstream repo.
4. Update `obs/_multibuild` with the PG major versions to build for.
5. Update `rpm/*.spec` and `debian/control` — keep `@BUILD_FLAVOR@` placeholders.
6. Sync to OBS:
   ```sh
   ./percona-obs -P local sync push ppg:17.9 my-pg-extension
   ```

---
