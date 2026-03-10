Format: 3.0 (quilt)
Source: percona-postgis
Binary: percona-postgis percona-postgis-doc percona-postgresql-17-postgis-3 percona-postgresql-17-postgis-3-scripts percona-postgresql-postgis percona-postgresql-postgis-scripts
Architecture: any all
Version: 1.0.0
Maintainer: Percona Development Team <info@percona.com>
Build-Depends:
 bison,
 dctrl-tools,
 debhelper-compat (= 13),
 dpkg-dev (>= 1.16.1~),
 flex,
 libcunit1-dev,
 libgdal-dev (>= 1.11.2+dfsg-3~) | libgdal1-dev (>= 1.9.0~),
 libgeos-dev (>= 3.6),
 libjson-c-dev | libjson0-dev (>= 0.9~),
 libpcre2-dev,
 libprotobuf-c-dev,
 libxml2-dev (>= 2.5.0~),
 lsb-release,
 pkgconf,
 po-debconf,
 percona-postgresql-common (>= 148~),
 percona-postgresql-server-dev-all,
 protobuf-c-compiler,
 rdfind
Debtransform-Release: 1
Debtransform-Files-Tar: debian.tar.gz
