# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from spack_repo.builtin_mock.build_systems.generic import Package

from spack.package import *


class ModuleSetenvRaw(Package):
    homepage = "http://www.spack.llnl.gov"
    url = "http://www.spack.llnl.gov/module-setenv-raw-1.0.tar.gz"

    version("1.0", "0123456789abcdef0123456789abcdef")

    def setup_run_environment(self, env: EnvironmentModifications) -> None:
        env.set("FOO", "{{name}}, {name}, {{}}, {}", raw=True)
