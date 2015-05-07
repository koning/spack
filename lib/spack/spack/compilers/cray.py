##############################################################################
# Copyright (c) 2013, Lawrence Livermore National Security, LLC.
# Produced at the Lawrence Livermore National Laboratory.
#
# This file is part of Spack.
# Written by Todd Gamblin, tgamblin@llnl.gov, All rights reserved.
# LLNL-CODE-647188
#
# For details, see https://scalability-llnl.github.io/spack
# Please also see the LICENSE file for our notice and the LGPL.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License (as published by
# the Free Software Foundation) version 2.1 dated February 1999.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the IMPLIED WARRANTY OF
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the terms and
# conditions of the GNU General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program; if not, write to the Free Software Foundation,
# Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307 USA
##############################################################################
import re
import sys
from spack.compiler import *
from sapck.util.executable import Executable

class Cray(Compiler):
    # Subclasses use possible names of C compiler
    cc_names = ['cc']

    # Subclasses use possible names of C++ compiler
    cxx_names = ['CC']

    # Subclasses use possible names of Fortran 77 compiler
    f77_names = ['ftn']

    # Subclasses use possible names of Fortran 90 compiler
    fc_names = ['ftn']

    @property
    def cxx11_flag(self):
        if 'Intel' in self.version:
            sver = self.version[5:]
            if sver < ver('11.1'):
                tty.die("Only intel 11.1 and above support c++11.")
            elif sver < ver('13'):
                return "-std=c++0x"
            else:
                return "-std=c++11"

        if 'PGI' in self.version:
            # FIXME
            sver = self.version[3:]
            if sver < ver('13'):
                tty.die("Only pgi 13.0 and above support c++11.")

            return "--c++11"

        if 'Gnu' in self.version:
            sver = self.version[3:]
            if sver < ver('4.3'):
                tty.die("Only gcc 4.3 and above support c++11.")
            elif sver < ver('4.7'):
                return "-std=gnu++0x"
            else:
                return "-std=gnu++11"

        if 'Cray' in self.version:
            # FIXME
            return ""


    @classmethod
    def default_version(cls, comp):
        """ The cray compilers use the PrgEnv module to determine 
            the actual compiler.  The Environment can define
            the intel, pgi, gnu or cray compilers to compile the
            the code for the login or compute nodes.
            This file is currently only set up for compute
            node compilation. A login node compile can use
            the base compiler directly.

            This function will loop through the various compiler types
            to determine the current base compiler. It is the users
            responsibility to ensure the PrgEnv matches the given
            requested spack compiler.

            The cray compiler sends version info to stderr instead of
            stdout. The cray output looks like this:
            <path to>/cc: INFO: compiling for compute nodes running CLE
            Cray C : Version 8.3.1 Tue May 05 2015 12:00:00
        """
        da = [('--version',r'\((?:GCC)\) ([^ ]+)','Gnu'),
              ('--version',r'\((?:IFORT|ICC)\) ([^ ]+)','Intel'),
              ('-V',r'(?:Cray .* Version) ([^ ]+)','Cray'),
              ('-V',r'pg[^ ]* ([^ ]+) \d\d\d?-bit target','PGI')]


        compiler = Executable(comp)
        for va,ra,st in da:
            output = None
            error = sys.stderr if st == 'Cray' else None

            try:
                output = compiler(va,return_output=True,error=error)
            except:
                pass

            if output:
                if re.search(ra,output):
                    return get_compiler_version(comp,va,ra,error,st)

        return 'unknown'


                


