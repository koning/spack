# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from spack_repo.builtin_mock.build_systems.generic import Package

from spack.package import *


class ModuleLongHelp(Package):
    """Package to test long description message generated in modulefile.
    Message too long is wrapped over multiple lines."""

    homepage = "http://www.spack.llnl.gov"
    url = "http://www.spack.llnl.gov/module-long-help-1.0.tar.gz"

    version("1.0", "0123456789abcdef0123456789abcdef")

    def setup_run_environment(self, env: EnvironmentModifications) -> None:
        env.set("FOO", "bar")
