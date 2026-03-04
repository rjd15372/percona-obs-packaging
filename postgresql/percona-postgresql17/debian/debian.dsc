Format: 3.0 (quilt)
Source: percona-postgresql-17
Binary: libpq-dev, libpq5, libecpg6, libecpg-dev, libecpg-compat3, libpgtypes3, percona-postgresql-17, percona-postgresql-client-17, percona-postgresql-server-dev-17, percona-postgresql-doc-17, percona-postgresql-plperl-17, percona-postgresql-plpython3-17, percona-postgresql-pltcl-17
Architecture: any all
Version: 17.6-1
Maintainer: Percona Development Team <info@percona.com>
Homepage: http://www.postgresql.org/
Standards-Version: 4.5.0
Debtransform-Release: 1
Debtransform-Tar: postgres-17.6.1.tar.gz
Testsuite: autopkgtest
Testsuite-Triggers: build-essential, debhelper, fakeroot, hunspell-en-us, iproute2, locales-all, logrotate, netcat-openbsd, perl, postgresql-common-dev, procps, tzdata, tzdata-legacy
Build-Depends: autoconf, bison, clang [!alpha !hppa !hurd-i386 !ia64 !kfreebsd-amd64 !kfreebsd-i386 !m68k !powerpc !riscv64 !sh4 !sparc64 !x32], debhelper, dh-exec (>= 0.13~), docbook-xml, docbook-xsl (>= 1.77), dpkg-dev (>= 1.16.1~), flex, gdb <!nocheck>, gettext, krb5-multidev, libedit-dev, libicu-dev, libio-pty-perl <!nocheck>, libipc-run-perl <!nocheck>, libkrb5-dev, libldap2-dev, liblz4-dev, libpam0g-dev | libpam-dev, libperl-dev, libreadline-dev, libselinux1-dev [linux-any], libssl-dev, libsystemd-dev [linux-any], libxml2-dev, libxml2-utils, libxslt1-dev, llvm-dev [!alpha !hppa !hurd-i386 !ia64 !kfreebsd-amd64 !kfreebsd-i386 !m68k !powerpc !riscv64 !sh4 !sparc64 !x32], mawk, perl (>= 5.8), pkg-config, python3-dev, systemtap-sdt-dev, tcl-dev, uuid-dev, xsltproc, zlib1g-dev, libcurl4, libcurl4-openssl-dev, curl | libz-dev
Package-List:
 libecpg-compat3 deb libs optional arch=any
 libecpg-dev deb libdevel optional arch=any
 libecpg6 deb libs optional arch=any
 libpgtypes3 deb libs optional arch=any
 libpq-dev deb libdevel optional arch=any
 libpq5 deb libs optional arch=any
 percona-postgresql-17 deb database optional arch=any
 percona-postgresql-client-17 deb database optional arch=any
 percona-postgresql-doc-17 deb doc optional arch=all
 percona-postgresql-plperl-17 deb database optional arch=any
 percona-postgresql-plpython3-17 deb database optional arch=any
 percona-postgresql-pltcl-17 deb database optional arch=any
 percona-postgresql-server-dev-17 deb libdevel optional arch=any
Checksums-Sha1:
 3838b9d9e67943bfb45d35585665386dcdce0ced 29780280 percona-postgresql-17_17.6.orig.tar.gz
 c7f45fb9bb5374a15bf44e474df9a99a4033eccd 25600 percona-postgresql-17_17.6-1.debian.tar.xz
Checksums-Sha256:
 1fbe575830da23e4d27423de84c13366994acb7f756005c48763c26bb3f64109 29780280 percona-postgresql-17_17.6.orig.tar.gz
 910b8160cb6925139faf3ffbd0e360c382a3f06c6da735ae67c6c7b7344828ba 25600 percona-postgresql-17_17.6-1.debian.tar.xz
Files:
 37e596d466c31ec9c630bdc610af4641 29780280 percona-postgresql-17_17.6.orig.tar.gz
 8ac17ed8c454ee68e062fa974c1e5b09 25600 percona-postgresql-17_17.6-1.debian.tar.xz
