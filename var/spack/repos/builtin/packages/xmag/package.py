# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from spack import *


class Xmag(AutotoolsPackage):
    """xmag displays a magnified snapshot of a portion of an X11 screen."""

    homepage = "http://cgit.freedesktop.org/xorg/app/xmag"
    url      = "https://www.x.org/archive/individual/app/xmag-1.0.6.tar.gz"

    version('1.0.6', sha256='07c5ec9114376dcd9a3303a38779e79b949d486f3b832d4a438550357d797aa5')

    depends_on('libxaw')
    depends_on('libxmu')
    depends_on('libxt')
    depends_on('libx11')

    depends_on('pkgconfig', type='build')
    depends_on('util-macros', type='build')