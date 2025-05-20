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
    install,
    join_path,
    mkdirp,
    register_builder,
    run_after,
    when,
    working_dir,
)

from ._checks import BuilderWithDefaults, execute_install_time_tests


class GoPackage(PackageBase):
    """Specialized class for packages built using the Go toolchain."""

    #: This attribute is used in UI queries that need to know the build
    #: system base class
    build_system_class = "GoPackage"

    #: Legacy buildsystem attribute used to deserialize and install old specs
    legacy_buildsystem = "go"

    build_system("go")

    with when("build_system=go"):
        depends_on("go", type="build")


@register_builder("go")
class GoBuilder(BuilderWithDefaults):
    """The Go builder encodes the most common way of building software with
    a golang go.mod file. It has two phases that can be overridden, if need be:

            1. :py:meth:`~.GoBuilder.build`
            2. :py:meth:`~.GoBuilder.install`

    For a finer tuning you may override:

        +-----------------------------------------------+--------------------+
        | **Method**                                    | **Purpose**        |
        +===============================================+====================+
        | :py:attr:`~.GoBuilder.build_args`             | Specify arguments  |
        |                                               | to ``go build``    |
        +-----------------------------------------------+--------------------+
        | :py:attr:`~.GoBuilder.check_args`             | Specify arguments  |
        |                                               | to ``go test``     |
        +-----------------------------------------------+--------------------+
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

    def setup_build_environment(self, env: EnvironmentModifications) -> None:
        env.set("GO111MODULE", "on")
        env.set("GOTOOLCHAIN", "local")
        env.set("GOPATH", join_path(self.pkg.stage.path, "go"))

    @property
    def build_directory(self):
        """Return the directory containing the main go.mod."""
        return self.pkg.stage.source_path

    @property
    def build_args(self):
        """Arguments for ``go build``."""
        # Pass ldflags -s = --strip-all and -w = --no-warnings by default
        return [
            "-p",
            str(self.pkg.module.make_jobs),
            "-modcacherw",
            "-ldflags",
            "-s -w",
            "-o",
            f"{self.pkg.name}",
        ]

    @property
    def check_args(self):
        """Argument for ``go test`` during check phase"""
        return []

    def build(self, pkg: GoPackage, spec: Spec, prefix: Prefix) -> None:
        """Runs ``go build`` in the source directory"""
        with working_dir(self.build_directory):
            pkg.module.go("build", *self.build_args)

    def install(self, pkg: GoPackage, spec: Spec, prefix: Prefix) -> None:
        """Install built binaries into prefix bin."""
        with working_dir(self.build_directory):
            mkdirp(prefix.bin)
            install(pkg.name, prefix.bin)

    run_after("install")(execute_install_time_tests)

    def check(self):
        """Run ``go test .`` in the source directory"""
        with working_dir(self.build_directory):
            self.pkg.module.go("test", *self.check_args)
