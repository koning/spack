# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from spack.package import (
    EnvironmentModifications,
    PackageBase,
    Prefix,
    Spec,
    build_system,
    depends_on,
    install_tree,
    register_builder,
    run_after,
    when,
    working_dir,
)

from ._checks import BuilderWithDefaults, execute_install_time_tests


class CargoPackage(PackageBase):
    """Specialized class for packages built using cargo."""

    #: This attribute is used in UI queries that need to know the build
    #: system base class
    build_system_class = "CargoPackage"

    build_system("cargo")

    with when("build_system=cargo"):
        depends_on("rust", type="build")


@register_builder("cargo")
class CargoBuilder(BuilderWithDefaults):
    """The Cargo builder encodes the most common way of building software with
    a rust Cargo.toml file. It has two phases that can be overridden, if need be:

            1. :py:meth:`~.CargoBuilder.build`
            2. :py:meth:`~.CargoBuilder.install`

    For a finer tuning you may override:

        +-----------------------------------------------+----------------------+
        | **Method**                                    | **Purpose**          |
        +===============================================+======================+
        | :py:meth:`~.CargoBuilder.build_args`          | Specify arguments    |
        |                                               | to ``cargo install`` |
        +-----------------------------------------------+----------------------+
        | :py:meth:`~.CargoBuilder.check_args`          | Specify arguments    |
        |                                               | to ``cargo test``    |
        +-----------------------------------------------+----------------------+
    """

    phases = ("build", "install")

    #: Names associated with package methods in the old build-system format
    legacy_methods = ("check", "installcheck")

    #: Names associated with package attributes in the old build-system format
    legacy_attributes = (
        "build_args",
        "check_args",
        "build_directory",
        "install_time_test_callbacks",
    )

    #: Callback names for install-time test
    install_time_test_callbacks = ["check"]

    @property
    def build_directory(self):
        """Return the directory containing the main Cargo.toml."""
        return self.pkg.stage.source_path

    @property
    def std_build_args(self):
        """Standard arguments for ``cargo build`` provided as a property for
        convenience of package writers."""
        return ["-j", str(self.pkg.module.make_jobs)]

    @property
    def build_args(self):
        """Arguments for ``cargo build``."""
        return []

    @property
    def check_args(self):
        """Argument for ``cargo test`` during check phase"""
        return []

    def setup_build_environment(self, env: EnvironmentModifications) -> None:
        env.set("CARGO_HOME", self.stage.path)

    def build(self, pkg: CargoPackage, spec: Spec, prefix: Prefix) -> None:
        """Runs ``cargo install`` in the source directory"""
        with working_dir(self.build_directory):
            pkg.module.cargo(
                "install", "--root", "out", "--path", ".", *self.std_build_args, *self.build_args
            )

    def install(self, pkg: CargoPackage, spec: Spec, prefix: Prefix) -> None:
        """Copy build files into package prefix."""
        with working_dir(self.build_directory):
            install_tree("out", prefix)

    run_after("install")(execute_install_time_tests)

    def check(self):
        """Run "cargo test"."""
        with working_dir(self.build_directory):
            self.pkg.module.cargo("test", *self.check_args)
