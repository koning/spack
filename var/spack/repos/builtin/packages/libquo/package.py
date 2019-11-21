# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from spack import *


class Libquo(AutotoolsPackage):

    """QUO (as in "status quo") is a runtime library that aids in accommodating
    thread-level heterogeneity in dynamic, phased MPI+X applications comprising
    single- and multi-threaded libraries."""

    homepage = "https://github.com/lanl/libquo"
    url      = "http://lanl.github.io/libquo/dists/libquo-1.3.tar.gz"
    git      = "https://github.com/lanl/libquo.git"

    version('develop', branch='master')
    version('1.3',   sha256='61b0beff15eae4be94b5d3cbcbf7bf757659604465709ed01827cbba45efcf90')
    version('1.2.9', sha256='0a64bea8f52f9eecd89e4ab82fde1c5bd271f3866c612da0ce7f38049409429b')

    depends_on('mpi')

    depends_on('m4',       when='@develop', type='build')
    depends_on('autoconf', when='@develop', type='build')
    depends_on('automake', when='@develop', type='build')
    depends_on('libtool',  when='@develop', type='build')

    @when('@develop')
    def autoreconf(self, spec, prefix):
        bash = which('bash')
        bash('./autogen')

    def configure_args(self):
        return [
            'CC={0}'.format(self.spec['mpi'].mpicc),
            'FC={0}'.format(self.spec['mpi'].mpifc)
        ]