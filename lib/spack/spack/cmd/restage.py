# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import argparse

import spack.cmd
import spack.llnl.util.tty as tty
from spack.cmd.common import arguments

description = "revert checked out package source code"
section = "build"
level = "long"


def setup_parser(subparser: argparse.ArgumentParser) -> None:
    arguments.add_common_arguments(subparser, ["specs"])


def restage(parser, args):
    if not args.specs:
        tty.die("spack restage requires at least one package spec.")

    specs = spack.cmd.parse_specs(args.specs, concretize=True)
    for spec in specs:
        spec.package.do_restage()
