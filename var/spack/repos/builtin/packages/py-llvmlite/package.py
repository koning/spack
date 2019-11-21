# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from spack import *


class PyLlvmlite(PythonPackage):
    """A lightweight LLVM python binding for writing JIT compilers"""

    homepage = "http://llvmlite.readthedocs.io/en/latest/index.html"
    url = "https://pypi.io/packages/source/l/llvmlite/llvmlite-0.23.0.tar.gz"

    version('0.27.1', sha256='48a1c3ae69fd8920cba153bfed8a46ac46474bc706a2100226df4abffe0000ab')
    version('0.26.0', sha256='13e84fe6ebb0667233074b429fd44955f309dead3161ec89d9169145dbad2ebf')
    version('0.25.0', sha256='fd64def9a51dd7dc61913a7a08eeba5b9785522740bec5a7c5995b2a90525025')
    version('0.23.0', sha256='bc8b1b46274d05b578fe9e980a6d98fa71c8727f6f9ed31d4d8468dce7aa5762')
    version('0.20.0', sha256='b2f174848df16bb9195a07fec102110a06d018da736bd9b3570a54d44c797c29')

    depends_on('py-setuptools', type='build')
    depends_on('python@2.6:2.8,3.4:', type=('build', 'run'))
    depends_on('py-enum34', type=('build', 'run'), when='^python@:3.3.99')
    depends_on('llvm@7.0:7.99', when='@0.27.0:')
    depends_on('llvm@6.0:6.99', when='@0.23.0:0.26.99')
    depends_on('llvm@4.0:4.99', when='@0.17.0:0.20.99')
    depends_on('binutils', type='build')