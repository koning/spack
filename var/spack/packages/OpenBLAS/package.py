from spack import *

class Openblas(Package):
    """OpenBLAS : An optimized BLAS library."""
    homepage = "http://http://www.openblas.net/"
    url      = "http://github.com/xianyi/OpenBLAS/archive/v0.2.14.tar.gz"

    version('0.2.14', '53cda7f420e1ba0ea55de536b24c9701')


    def install(self, spec, prefix):
        make()
        make("PREFIX=%s" % (prefix),"install")
