# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from spack_repo.builtin.build_systems.cmake import CMakePackage

from spack.package import *


class HipblasCommon(CMakePackage):
    """Common files shared by hipBLAS and hipBLASLt"""

    homepage = "https://github.com/ROCm/hipBLAS-common"
    url = "https://github.com/ROCm/hipBLAS-common/archive/refs/tags/rocm-6.3.0.tar.gz"

    maintainers("srekolam", "renjithravindrankannath", "afzpatel")

    license("MIT")

    version("6.4.0", sha256="8953bcf13ba1aa03cb29481bd90eaef373bf0e41cadff68e567ecd2ec0b07363")
    version("6.3.3", sha256="b2b77abb5c851674839b583dc313684b5f6aa676e8186ff0a5696b6962c2b4da")
    version("6.3.2", sha256="29aa1ac1a0f684a09fe2ea8a34ae8af3622c27708c7df403a7481e75174e1984")
    version("6.3.1", sha256="512e652483b5580713eca14db3fa633d0441cd7c02cdb0d26e631ea605b9231b")
    version("6.3.0", sha256="240bb1b0f2e6632447e34deae967df259af1eec085470e58a6d0aa040c8530b0")
