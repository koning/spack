# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from spack_repo.builtin.build_systems.cmake import CMakePackage

from spack.package import *


class Samurai(CMakePackage):
    """Intervals coupled with algebra of set to handle adaptive
    mesh refinement and operators on it"""

    homepage = "https://github.com/hpc-maths/samurai"
    git = "https://github.com/hpc-maths/samurai.git"

    maintainers("gouarin", "sbstndb")

    license("BSD-3-Clause")

    version("master", branch="master")
    version("0.23.0", tag="v0.23.0", commit="c0c01fe78aa81f6e630962a6d90f57df6b84cc08")
    version("0.22.0", tag="v0.22.0", commit="c32a011036437b3651a0b2df409d0ddd310f8f9f")
    version("0.21.1", tag="v0.21.1", commit="29798bd9592e5b80674c49080a242378a61878c3")
    version("0.20.0", tag="v0.20.0", commit="b894b8aebf992e112e129e3b70ccdfc7c19c6647")
    version("0.19.0", tag="v0.19.0", commit="b35332c5d719a940b045210abd8e4eb9b69f6b85")
    version("0.18.0", tag="v0.18.0", commit="483c44d1b28dc59e2004d618b094b4de31e1ef7c")

    variant("mpi", default=False, description="Enable MPI support")
    variant("openmp", default=False, description="Enable OpenMP support")
    # variants for a future release
    # variant("demos", default=False, description="Build Demos")
    # variant("benchmarks", default=False,description="Build benchmarks")
    variant("tests", default=False, description="Build tests")
    variant("check_nan", default=False, description="Check for Nan in computations")

    depends_on("xtl@0.7.4")
    # optional dependency for a future release
    # depends_on("xsimd@11.0.0")
    depends_on("xtensor@0.24.1 ~tbb")
    depends_on("highfive~mpi", when="~mpi")
    depends_on("highfive+mpi", when="+mpi")
    depends_on("pugixml")
    depends_on("fmt")
    depends_on("nlohmann-json")
    depends_on("cli11")
    depends_on("cxxopts")
    depends_on("cgal")
    depends_on("petsc~mpi", when="~mpi")
    depends_on("petsc+mpi", when="+mpi")
    depends_on("boost+serialization+mpi", when="+mpi")

    def setup_dependent_build_environment(
        self, env: EnvironmentModifications, dependent_spec: Spec
    ) -> None:
        include_path = self.spec.prefix.include
        env.append_path("CXXFLAGS", f"-I{include_path}")

    def setup_run_environment(self, env: EnvironmentModifications) -> None:
        env.prepend_path("CPATH", self.spec.prefix.include)

    def cmake_args(self):
        spec = self.spec
        options = []

        options.append(self.define_from_variant("SAMURAI_CHECK_NAN", "check_nan"))

        # MPI support
        if spec.satisfies("+mpi"):
            options.append(self.define_from_variant("WITH_MPI", "mpi"))
            options.append(self.define("HDF5_IS_PARALLEL", True))

        # OpenMP support
        if spec.satisfies("+openmp"):
            options.append(self.define_from_variant("WITH_OPENMP", "openmp"))

        return options
