Format: 3.0 (native)
Source: percona-postgresql-common
Binary: percona-postgresql-common, percona-postgresql-common-dev, postgresql-common, postgresql-client-common, percona-postgresql-server-dev-all, percona-postgresql, percona-postgresql-client, percona-postgresql-doc, percona-postgresql-contrib, percona-postgresql-all
Architecture: all
Version: 280
Debtransform-Release: 1
Maintainer: Percona Development Team <info@percona.com>
Testsuite: autopkgtest
Testsuite-Triggers: build-essential, debhelper, fakeroot, hunspell-en-us, iproute2, locales-all, logrotate, netcat-openbsd, perl, postgresql, postgresql-all, postgresql-doc, procps
Build-Depends: debhelper (>= 9), debhelper (>= 10.1) | dh-systemd (>= 1.19), libreadline-dev
Package-List:
 percona-postgresql deb database optional arch=all
 percona-postgresql-all deb database optional arch=all
 percona-postgresql-client deb database optional arch=all
 percona-postgresql-common deb database optional arch=all
 percona-postgresql-common-dev deb database optional arch=all
 percona-postgresql-contrib deb database optional arch=all
 percona-postgresql-doc deb doc optional arch=all
 percona-postgresql-server-dev-all deb database optional arch=all
 postgresql-client-common deb database optional arch=all
 postgresql-common deb database optional arch=all
