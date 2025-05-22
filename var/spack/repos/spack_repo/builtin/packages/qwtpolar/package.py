# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from spack_repo.builtin.build_systems.qmake import QMakePackage

from spack.package import *


class Qwtpolar(QMakePackage):
    """The QwtPolar library contains classes for displaying values on a polar
    coordinate system.
    """

    homepage = "https://qwtpolar.sourceforge.io"
    url = "https://sourceforge.net/projects/qwtpolar/files/qwtpolar/1.1.1/qwtpolar-1.1.1.tar.bz2"

    version("1.1.1", sha256="6168baa9dbc8d527ae1ebf2631313291a1d545da268a05f4caa52ceadbe8b295")

    depends_on("cxx", type="build")  # generated

    depends_on("qt@4.4:")
    depends_on("qwt@6.1:")
    # qwtpolar merged with qwt as of qwt@6.2
    # as a result, it doesn't build with qwt@6.2:
    depends_on("qwt@:6.1", when="@1.1.1")

    def patch(self):
        # Modify hardcoded prefix
        filter_file(
            r"/usr/local/qwtpolar-\$\$QWT_POLAR_VERSION.*", self.prefix, "qwtpolarconfig.pri"
        )
        # Don't build examples as they're causing qmake to throw errors
        filter_file(r"QwtPolarExamples", "", "qwtpolarconfig.pri")
