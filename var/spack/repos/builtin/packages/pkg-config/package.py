# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from spack import *


class PkgConfig(AutotoolsPackage):
    """pkg-config is a helper tool used when compiling applications
    and libraries"""

    homepage = "http://www.freedesktop.org/wiki/Software/pkg-config/"
    url = "http://pkgconfig.freedesktop.org/releases/pkg-config-0.29.2.tar.gz"

    version('0.29.2', sha256='6fc69c01688c9458a57eb9a1664c9aba372ccda420a02bf4429fe610e7e7d591')
    version('0.29.1', sha256='beb43c9e064555469bd4390dcfd8030b1536e0aa103f08d7abf7ae8cac0cb001')
    version('0.28',   sha256='6b6eb31c6ec4421174578652c7e141fdaae2dabad1021f420d8713206ac1f845')

    provides('pkgconfig')

    variant('internal_glib', default=True,
            description='Builds with internal glib')

    # The following patch is needed for gcc-6.1
    patch('g_date_strftime.patch', when='@:0.29.1')

    parallel = False

    def setup_dependent_environment(self, spack_env, run_env, dependent_spec):
        """Adds the ACLOCAL path for autotools."""
        spack_env.append_path('ACLOCAL_PATH',
                              join_path(self.prefix.share, 'aclocal'))

    def configure_args(self):
        config_args = ['--enable-shared']

        if '+internal_glib' in self.spec:
            # There's a bootstrapping problem here;
            # glib uses pkg-config as well, so break
            # the cycle by using the internal glib.
            config_args.append('--with-internal-glib')

        return config_args