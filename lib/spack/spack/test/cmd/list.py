# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import os
import pathlib
import sys
from textwrap import dedent

import pytest

import spack.paths
import spack.repo
from spack.main import SpackCommand

pytestmark = [pytest.mark.usefixtures("mock_packages")]

list = SpackCommand("list")


def test_list():
    output = list()
    assert "bzip2" in output
    assert "hdf5" in output


def test_list_cli_output_format(mock_tty_stdout):
    out = list("mpileaks")
    # Currently logging on Windows detaches stdout
    # from the terminal so we miss some output during tests
    # TODO: (johnwparent): Once logging is amended on Windows,
    # restore this test
    if not sys.platform == "win32":
        out_str = dedent(
            """\
    mpileaks
    ==> 1 packages
    """
        )
    else:
        out_str = dedent(
            """\
        mpileaks
        """
        )
    assert out == out_str


def test_list_filter():
    output = list("py-*")
    assert "py-extension1" in output
    assert "py-extension2" in output
    assert "py-extension3" in output
    assert "python" not in output
    assert "mpich" not in output

    output = list("py")
    assert "py-extension1" in output
    assert "py-extension2" in output
    assert "py-extension3" in output
    assert "python" in output
    assert "mpich" not in output


def test_list_search_description():
    output = list("--search-description", "one build dependency")
    assert "depb" in output


def test_list_format_name_only():
    output = list("--format", "name_only")
    assert "zmpi" in output
    assert "hdf5" in output


def test_list_format_version_json():
    output = list("--format", "version_json")
    assert '{"name": "zmpi",' in output
    assert '{"name": "dyninst",' in output
    import json

    json.loads(output)


def test_list_format_html():
    output = list("--format", "html")
    assert '<div class="section" id="zmpi">' in output
    assert "<h1>zmpi" in output

    assert '<div class="section" id="hdf5">' in output
    assert "<h1>hdf5" in output


def test_list_update(tmp_path: pathlib.Path):
    update_file = tmp_path / "output"

    # not yet created when list is run
    list("--update", str(update_file))
    assert update_file.exists()
    with update_file.open() as f:
        assert f.read()

    # created but older than any package
    with update_file.open("w") as f:
        f.write("empty\n")
    os.utime(str(update_file), (0, 0))  # Set mtime to 0
    list("--update", str(update_file))
    assert update_file.exists()
    with update_file.open() as f:
        assert f.read() != "empty\n"

    # newer than any packages
    with update_file.open("w") as f:
        f.write("empty\n")
    list("--update", str(update_file))
    assert update_file.exists()
    with update_file.open() as f:
        assert f.read() == "empty\n"


def test_list_tags():
    output = list("--tag", "tag1")
    assert "mpich" in output
    assert "mpich2" in output

    output = list("--tag", "tag2")
    assert "mpich\n" in output
    assert "mpich2" not in output

    output = list("--tag", "tag3")
    assert "mpich\n" not in output
    assert "mpich2" in output


def test_list_count():
    output = list("--count")
    assert int(output.strip()) == len(spack.repo.all_package_names())

    output = list("--count", "py-")
    assert int(output.strip()) == len(
        [name for name in spack.repo.all_package_names() if "py-" in name]
    )


def test_list_repos():
    with spack.repo.use_repositories(
        os.path.join(spack.paths.test_repos_path, "spack_repo", "builtin_mock"),
        os.path.join(spack.paths.test_repos_path, "spack_repo", "builder_test"),
    ):
        total_pkgs = len(list().strip().split())
        mock_pkgs = len(list("-r", "builtin_mock").strip().split())
        builder_pkgs = len(list("-r", "builder_test").strip().split())
        both_repos = len(list("-r", "builtin_mock", "-r", "builder_test").strip().split())

        assert total_pkgs > mock_pkgs > builder_pkgs
        assert both_repos == total_pkgs
