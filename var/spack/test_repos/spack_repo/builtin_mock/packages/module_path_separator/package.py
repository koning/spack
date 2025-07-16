# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from spack_repo.builtin_mock.build_systems.generic import Package

from spack.package import *


class ModulePathSeparator(Package):
    homepage = "http://www.spack.llnl.gov"
    url = "http://www.spack.llnl.gov/module-path-separator-1.0.tar.gz"

    version("1.0", md5="0123456789abcdef0123456789abcdef")

    def setup_run_environment(self, env: EnvironmentModifications) -> None:
        env.append_path("COLON", "foo")
        env.prepend_path("COLON", "foo")
        env.remove_path("COLON", "foo")

        env.append_path("SEMICOLON", "bar", separator=";")
        env.prepend_path("SEMICOLON", "bar", separator=";")
        env.remove_path("SEMICOLON", "bar", separator=";")

        env.append_flags("SPACE", "qux")
        env.remove_flags("SPACE", "qux")
