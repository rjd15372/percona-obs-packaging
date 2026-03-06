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

For the examples in the rest of this document a shell alias is used for brevity:

```sh
alias obs='./percona-obs -P local'
```

---

## Examples

### Preview all changes without writing to OBS

```sh
obs sync --dry-run
```

Shows everything that would be created or updated. Nothing is written to OBS.

### Preview changes and also run local services (Go vendoring, etc.)

```sh
obs sync --dry-run-remote
```

Same as `--dry-run` but also runs `mode="manual"` services locally, letting you verify
that tools like `go_modules` work correctly before committing anything to OBS.

### Sync all packages

```sh
obs sync
```

Walks the entire `root/` tree, creates or updates all OBS projects and packages, and
uploads any changed `obs/` files as a single revision per package.

### Sync a single standalone package

```sh
obs sync percona-telemetry-agent
```

### Sync a single PostgreSQL extension

PG extensions live under a subproject (`ppg/17.9/`). Pass the subproject and package
name separately:

```sh
obs sync ppg:17.9 percona-pg-telemetry
```

### Sync all packages under a subproject

```sh
obs sync ppg:17.9
```

---

## Triggering and monitoring builds

### Trigger a rebuild

```sh
obs build trigger                        # all packages
obs build trigger ppg:17.9              # all packages under a subproject
obs build trigger ppg:17.9 etcd         # single package
```

Sends an OBS service run request (`runservice`) for each targeted package, causing
OBS to re-fetch sources and queue a new build.

### Check build status

```sh
obs build status
```

Prints a color-coded tree of live build statuses fetched from OBS:

```
home:Admin:percona
├── percona-telemetry-agent
│   ├── RockyLinux_9           ✔ succeeded
│   ├── Debian_13              ✔ succeeded
│   └── xUbuntu_24.04          ✔ succeeded
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
        │   ├── RockyLinux_9   ✔ succeeded
        │   ├── Debian_13      ✔ succeeded
        │   └── xUbuntu_24.04  ✔ succeeded
        └── percona-pg-telemetry
            ├── RockyLinux_9   ✔ succeeded  [:17]
            ├── Debian_13      ✔ succeeded  [:17]
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
obs build status ppg:17.9               # subproject only (tree rooted there)
obs build status ppg:17.9 etcd          # single package
```

Set `NO_COLOR=1` to disable color output.

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
   obs sync my-new-service
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
   obs sync ppg:17.9 my-pg-extension
   ```

---
