# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from spack import *


class PyPydoe(PythonPackage):
    """Design of experiments for Python"""

    homepage = "https://pypi.org/project/pyDOE/"
    url      = "https://pypi.io/packages/source/p/pyDOE/pyDOE-0.3.8.zip"

    version('0.3.8', sha256='cbd6f14ae26d3c9f736013205f53ea1191add4567033c3ee77b7dd356566c4b6')

    depends_on('py-setuptools', type='build')
    depends_on('py-numpy', type='run')
    depends_on('py-scipy', type='run')
