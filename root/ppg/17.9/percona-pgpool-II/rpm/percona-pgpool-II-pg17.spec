# NOTE: This spec is based on the upstream pgpool2 src/pgpool.spec,
# modified to match Percona's naming and build conventions.
# The builder script (pgpool2_builder.sh) applies the following transformations
# at build time from the embedded spec in the upstream source tree:
#   - s/pgpool-II/percona-pgpool-II/g
#   - short_name kept as pgpool-II
#   - libtoolize; autoreconf added to configure step
#   - PG major version macros injected
#
# This placeholder spec is sufficient to register the package in OBS;
# the actual build uses the spec bundled in the upstream source tarball.

%define short_name      pgpool-II
%define pgmajorversion  17
%define pghome          /usr/pgsql-%{pgmajorversion}

Summary:        pgpool-II connection pooling server for PostgreSQL %{pgmajorversion}
Name:           percona-pgpool-II-pg%{pgmajorversion}
Version:        1.0.0
Release:        1%{?dist}
License:        BSD
URL:            https://www.pgpool.net/
Source0:        pgpool2-%{version}.tar.gz

BuildRequires:  postgresql%{pgmajorversion}-devel
BuildRequires:  pam-devel
BuildRequires:  libmemcached-devel
BuildRequires:  openssl-devel
BuildRequires:  libtool
BuildRequires:  autoconf
BuildRequires:  automake
BuildRequires:  gcc

Requires:       postgresql%{pgmajorversion} >= %{pgmajorversion}

%description
pgpool-II is a middleware that works between PostgreSQL servers and a
PostgreSQL database client. It provides connection pooling, replication,
load balancing, and limiting of exceeding connections.

%prep
%setup -q -n pgpool2-%{version}

%build
libtoolize
autoreconf --force --install
%configure \
    --with-pgsql=%{pghome} \
    --with-pgsql-includedir=%{pghome}/include/ \
    --with-openssl \
    --with-pam
make %{?_smp_mflags}

%install
make install DESTDIR=%{buildroot}

%files
%doc README TODO COPYING
%{_bindir}/*
%{_sysconfdir}/%{short_name}/*.sample
%{_mandir}/man8/*

%changelog
* Mon Mar 10 2026 Percona Build/Release Team <eng-build@percona.com> - 4.7.0-1
- Release 4.7.0-1
