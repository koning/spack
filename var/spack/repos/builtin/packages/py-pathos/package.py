# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from spack import *


class PyPathos(PythonPackage):
    """Parallel graph management and execution in heterogeneous computing """

    homepage = "https://github.com/uqfoundation/pathos"
    url      = "https://pypi.io/packages/source/p/pathos/pathos-0.2.0.zip"

    version('0.2.0', sha256='2f4e67e7914c95fb0cce766bab173eb2c5860ee420108fa183099557ac2e50e9')

    depends_on('python@2.6:2.8,3.1:')

    depends_on('py-setuptools@0.6:', type='build')
    depends_on('py-multiprocess@0.70.4:', type=('build', 'run'))
    depends_on('py-pox@0.2.2:', type=('build', 'run'))
    depends_on('py-ppft@1.6.4.5:', type=('build', 'run'))
    depends_on('py-dill@0.2.5:', type=('build', 'run'))