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
│   ├── RockyLinux_9           ✔ succeeded  3.5.26-6.1
│   ├── Debian_13              ✔ succeeded  3.5.26-6.1
│   └── xUbuntu_24.04          ✔ succeeded  3.5.26-6.1
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
        │   ├── RockyLinux_9   ✔ succeeded  3.5.26-6.1
        │   ├── Debian_13      ✔ succeeded  3.5.26-6.1
        │   └── xUbuntu_24.04  ✔ succeeded  3.5.26-6.1
        └── percona-pg-telemetry
            ├── RockyLinux_9   ✔ succeeded  [:17]  1.0.0-1.1
            ├── Debian_13      ✔ succeeded  [:17]  1.0.0-1.1
            └── xUbuntu_24.04  ◌ scheduled  [:17]
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
```

Set `NO_COLOR=1` to disable color output.

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
