# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)
from spack.package import (
    PackageBase,
    Prefix,
    Spec,
    build_system,
    depends_on,
    install_tree,
    register_builder,
    when,
    which,
    working_dir,
)

from ._checks import BuilderWithDefaults


class MavenPackage(PackageBase):
    """Specialized class for packages that are built using the
    Maven build system. See https://maven.apache.org/index.html
    for more information.
    """

    # To be used in UI queries that require to know which
    # build-system class we are using
    build_system_class = "MavenPackage"

    #: Legacy buildsystem attribute used to deserialize and install old specs
    legacy_buildsystem = "maven"

    build_system("maven")

    with when("build_system=maven"):
        depends_on("java", type=("build", "run"))
        depends_on("maven", type="build")


@register_builder("maven")
class MavenBuilder(BuilderWithDefaults):
    """The Maven builder encodes the default way to build software with Maven.
    It has two phases that can be overridden, if need be:

        1. :py:meth:`~.MavenBuilder.build`
        2. :py:meth:`~.MavenBuilder.install`
    """

    phases = ("build", "install")

    #: Names associated with package methods in the old build-system format
    legacy_methods = ("build_args",)

    #: Names associated with package attributes in the old build-system format
    legacy_attributes = ("build_directory",)

    @property
    def build_directory(self):
        """The directory containing the ``pom.xml`` file."""
        return self.pkg.stage.source_path

    def build_args(self):
        """List of args to pass to build phase."""
        return []

    def build(self, pkg: MavenPackage, spec: Spec, prefix: Prefix) -> None:
        """Compile code and package into a JAR file."""
        with working_dir(self.build_directory):
            mvn = which("mvn", required=True)
            if self.pkg.run_tests:
                mvn("verify", *self.build_args())
            else:
                mvn("package", "-DskipTests", *self.build_args())

    def install(self, pkg: MavenPackage, spec: Spec, prefix: Prefix) -> None:
        """Copy to installation prefix."""
        with working_dir(self.build_directory):
            install_tree(".", prefix)
