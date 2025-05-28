# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)
from spack_repo.builtin_mock.build_systems.generic import Package

from spack.package import *


class TrivialInstallTestDependent(Package):
    """This package is a stub with a trivial install method.  It allows us
    to test the install and uninstall logic of spack."""

    homepage = "http://www.example.com/trivial_install"
    url = "http://www.unit-test-should-replace-this-url/trivial_install-1.0.tar.gz"

    version("1.0", md5="0123456789abcdef0123456789abcdef")

    depends_on("trivial-install-test-package")

    def install(self, spec, prefix):
        touch(join_path(prefix, "an_installation_file"))
