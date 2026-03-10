# Packaging Design Decisions by Package

## percona-pgbouncer

- **Source naming**: `obs/_service` uses `filename: percona-pgbouncer` because the upstream repo
  is named `pgbouncer` (not `percona-pgbouncer`). This ensures the tarball is named
  `percona-pgbouncer-<version>.tar.gz` to match `Source0` in the RPM spec.
- **Upstream tag format**: pgbouncer tags use underscores (`pgbouncer_1_25_1`) rather than
  dots. The `revision` in `_service` must match the exact upstream git tag.
- **Debian source format**: `3.0 (native)` used (not quilt) because we're not applying patches
  on top of an upstream tarball; OBS handles the source assembly.
- **debian/source/options**: `extend-diff-ignore = rpm/` added so the Debian build system
  ignores the `rpm/` directory that is included via debtransform.

## percona-pgbackrest

- **Multiple source files**: The spec has `Source1` (pgbackrest.conf), `Source2`
  (pgbackrest-tmpfiles.d), `Source3` (pgbackrest.logrotate), `Source4` (pgbackrest.service).
  These are included in the `rpm/` directory and referenced by name in the spec.
- **Build system**: pgbackrest uses meson (not make/autoconf). `%meson`, `%meson_build`, and
  `%meson_install` macros are used in the spec. `libssh2=disabled` is passed to meson
  because it's not always available in all distros.
- **Sysusers/postgres user**: The `%pre` section creates the `postgres` user (uid 26, gid 26)
  following the Percona convention for all PG-adjacent packages.
- **SUSE vs RHEL distinction**: Conditional `%if 0%{?suse_version}` blocks handle different
  package names for openssl and lz4 between SUSE and RHEL/Fedora distros.
- **Debian binary package**: Includes `percona-pgbackrest` (main) and `percona-pgbackrest-doc`
  (documentation subpackage).

## percona-pgaudit_set_user

- **BuildRequires needs -devel**: The spec initially had `BuildRequires: percona-postgresql17`
  (the server package). The build requires `percona-postgresql17-devel` to get pgxs.mk and
  the headers needed to compile PostgreSQL extensions. **Fix**: added `-devel` suffix.
- **pginstdir trailing slash**: The spec defines `%define pginstdir /usr/pgsql-%{pgmajorversion}/`
  (with trailing slash). Files section entries use `%{pginstdir}/lib/...` producing paths
  like `/usr/pgsql-17//lib/` — this works on RPM but is slightly inconsistent. Left as-is
  since it matches upstream packaging.
- **Requires postgresql vs percona-postgresql**: `Requires: postgresql%{pgmajorversion}` (not
  percona-) is intentional — satisfies the dependency whether using upstream or Percona PG.

## percona-wal2json

- **Missing pginstdir**: The spec used `%{pginstdir}` in both `%install` and `%files` sections
  but never defined the macro. RPM was treating it as an empty string, causing "File must
  begin with /" errors. **Fix**: added `%define pginstdir /usr/pgsql-%{pgmajorversion}` near
  the top of the spec.
- **Patch for pg_config path**: `wal2json-pg17-makefile-pgxs.patch` patches the Makefile to
  set `PG_CONFIG = /usr/pgsql-17/bin/pg_config`. This is how wal2json finds the PostgreSQL
  installation at build time.
- **doc via %doc macro**: The README is moved to `%{pginstdir}/doc/extension/` in `%install`
  and listed with `%doc` in `%files`. This caused a secondary issue because `%doc` in older
  RPM macros copies from the BUILD directory, not BUILDROOT; the path expansion
  `%{pginstdir}/doc/...` was being treated literally. The `%doc %{pginstdir}/doc/...` form
  is fine in newer RPM versions where `%{pginstdir}` is expanded.

## percona-pgpool-II

### Debian/Ubuntu
- **Dual compat conflict**: `debian/compat` contained `13` AND `debian/control` had
  `debhelper-compat (= 13)` in Build-Depends. Modern debhelper (>= 12) requires specifying
  the compat level exactly once. **Fix**: removed `debian/compat` file; kept the
  `debhelper-compat (= 13)` build-dep in `debian/control`.
- **Build-Depends**: Does not include `percona-postgresql17-devel` because pgpool-II connects
  to PostgreSQL via libpq (client lib), not server headers. Uses `libpq-dev` instead.

### RockyLinux_9 RPM
- **Bogus changelog date**: `Mon Mar 10 2026` was wrong; March 10, 2026 is a Tuesday.
  RPM rejects changelogs with wrong day-of-week. **Fix**: changed to `Tue Mar 10 2026`.
- **sysconfdir for sample configs**: pgpool-II's `make install` installs sample configs
  (`pgpool.conf.sample`, `pcp.conf.sample`, `pool_hba.conf.sample`) to `$(sysconfdir)/`.
  With the default `%configure` macro, `--sysconfdir=/etc` puts them in `/etc/` directly,
  but the spec's `%files` expected them at `/etc/pgpool-II/*.sample`. **Fix**: added
  `--sysconfdir=%{_sysconfdir}/%{short_name}` to the `%configure` call so configs install
  to `/etc/pgpool-II/`. This also sets the default config search path for pgpool at runtime.
- **Man pages removed from %files**: pgpool-II 4.7 does not install man pages during
  `make install` without additional doc build tooling (jade/opensp). Removed
  `%{_mandir}/man8/*` from `%files` to avoid "file not found" errors.
- **autoreconf required**: pgpool-II's configure.ac is not pre-generated in the git checkout,
  so `libtoolize && autoreconf --force --install` must run before `%configure`. This is
  needed because we build directly from the upstream git tag.

## percona-postgis

- **Version naming problem (unresolved)**: The RPM spec is named `percona-postgis35_17.spec`.
  OBS `set_version` service appears to extract `17.spec` from the filename and uses it as the
  version in `debian.dsc`, producing a malformed version `17.spec-1+X.Y`. Root cause: OBS
  `set_version` strips the package name prefix from the spec filename to detect a version
  component; when the name doesn't match cleanly, it falls back to using a trailing segment
  of the filename. Potential fix: rename spec to `percona-postgis.spec`.
- **Missing GIS library dependencies**: Both Debian and RockyLinux_9 builds fail because the
  OBS instance does not mirror the specialized GIS library packages required:
  - Debian: `libgdal-dev`, `libgeos-dev`, `libproj-dev`, `libsfcgal-dev`, etc.
  - RPM: `geos311-devel`, `gdal311-devel`, `proj95-devel` (pgdg-style versioned packages)
  These packages come from dedicated GIS repositories (OSGeo, pgdg-extras) that are not
  configured in our OBS instance. PostGIS builds are blocked until these repos are added.
- **Status**: Left failing; requires OBS repository configuration changes that are outside
  the scope of packaging file fixes.

## percona-pg_repack (fixed by user prior to this session)

- No design decisions captured; fixes were applied directly by the user.

## percona-pgbadger

- **Perl-only package**: pgbadger is a pure-Perl script. No compilation step required.
  Debian build just installs the script; RPM build uses `%{__install}` for the script.

## General OBS Patterns Used

- **`obs/_service` structure**: All packages use a combination of `obs_scm` (to fetch debian/
  and rpm/ files from this git repo) + `obs_scm` (to fetch upstream source) + `tar` +
  `recompress` + `set_version` services.
- **`version: _none_`**: Used for the debian/ and rpm/ obs_scm entries so `set_version` picks
  up the version only from the upstream source tarball obsinfo.
- **`filename` param**: Used to set the base name of the tarball produced by obs_scm. Critical
  when the upstream repo name differs from the Percona package name.
- **`Debtransform-Files-Tar`**: Used in `debian.dsc` to explicitly declare which tarballs
  debtransform should include (debian.tar.gz and rpm.tar.gz).
- **`extend-diff-ignore = rpm/`**: Added to `debian/source/options` to prevent Debian build
  tools from complaining about the `rpm/` directory being present in the source tree.
- **RPM `Version: 1.0.0` placeholder**: All RPM specs start with `Version: 1.0.0`. OBS
  `set_version` service replaces this with the actual version from the upstream source
  obsinfo at build time.
- **Epoch: 1**: Added to RPM specs for packages that may have been previously packaged
  without Percona naming, to ensure upgrade paths work correctly.
