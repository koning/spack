# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from spack import *


class Abyss(AutotoolsPackage):
    """ABySS is a de novo, parallel, paired-end sequence assembler
       that is designed for short reads. The single-processor version
       is useful for assembling genomes up to 100 Mbases in size."""

    homepage = "http://www.bcgsc.ca/platform/bioinfo/software/abyss"
    url      = "https://github.com/bcgsc/abyss/releases/download/1.5.2/abyss-1.5.2.tar.gz"

    version('2.1.4', sha256='2145a1727556104d6a14db06a9c06f47b96c31cc5ac595ae9c92224349bdbcfc')
    version('2.0.2', sha256='d87b76edeac3a6fb48f24a1d63f243d8278a324c9a5eb29027b640f7089422df')
    version('1.5.2', sha256='8a52387f963afb7b63db4c9b81c053ed83956ea0a3981edcad554a895adf84b1')

    variant('maxk', values=int, default=0,
            description='''set the maximum k-mer length.
            This value must be a multiple of 32''')

    depends_on('autoconf', type='build')
    depends_on('automake', type='build')
    depends_on('bwa', type='run')

    depends_on('mpi')
    depends_on('boost@:1.50.0,1.53.0:', when='@2.0.2:')
    depends_on('boost@:1.50.0,1.53.0:1.59.0', when='@:1.5.2')
    depends_on('sparsehash')
    depends_on('sqlite')
    depends_on('libtool')

    conflicts('^intel-mpi')
    conflicts('^intel-parallel-studio+mpi')
    conflicts('^mvapich2')
    conflicts('^spectrum-mpi')

    def configure_args(self):
        maxk = int(self.spec.variants['maxk'].value)
        args = ['--with-boost=%s' % self.spec['boost'].prefix,
                '--with-sqlite=%s' % self.spec['sqlite'].prefix,
                '--with-mpi=%s' % self.spec['mpi'].prefix]
        if maxk:
            args.append('--enable-maxk=%s' % maxk)
        if self.spec['mpi'].name == 'mpich':
            args.append('--enable-mpich')
        return args