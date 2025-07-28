# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)
import filecmp
import glob
import gzip
import io
import json
import os
import pathlib
import re
import tarfile
import urllib.error
import urllib.request
import urllib.response
from pathlib import Path, PurePath
from typing import Any, Callable, Dict, NamedTuple, Optional

import pytest

import spack.binary_distribution
import spack.concretize
import spack.config
import spack.hooks.sbang as sbang
import spack.main
import spack.mirrors.mirror
import spack.oci.image
import spack.spec
import spack.stage
import spack.store
import spack.util.gpg
import spack.util.spack_yaml as syaml
import spack.util.url as url_util
import spack.util.web as web_util
from spack.binary_distribution import CannotListKeys, GenerateIndexError
from spack.database import INDEX_JSON_FILE
from spack.installer import PackageInstaller
from spack.llnl.util.filesystem import join_path, readlink, working_dir
from spack.spec import Spec
from spack.url_buildcache import (
    INDEX_MANIFEST_FILE,
    BuildcacheComponent,
    BuildcacheEntryError,
    URLBuildcacheEntry,
    URLBuildcacheEntryV2,
    compression_writer,
    get_url_buildcache_class,
    get_valid_spec_file,
)

pytestmark = pytest.mark.not_on_windows("does not run on windows")

mirror_cmd = spack.main.SpackCommand("mirror")
install_cmd = spack.main.SpackCommand("install")
uninstall_cmd = spack.main.SpackCommand("uninstall")
buildcache_cmd = spack.main.SpackCommand("buildcache")


@pytest.fixture
def dummy_prefix(tmp_path: pathlib.Path):
    """Dummy prefix used for testing tarball creation, validation, extraction"""
    p = tmp_path / "prefix"
    p.mkdir()
    assert os.path.isabs(str(p))

    (p / "bin").mkdir()
    (p / "share").mkdir()
    (p / ".spack").mkdir()

    app = p / "bin" / "app"
    relative_app_link = p / "bin" / "relative_app_link"
    absolute_app_link = p / "bin" / "absolute_app_link"
    data = p / "share" / "file"

    with open(app, "w", encoding="utf-8") as f:
        f.write("hello world")

    with open(data, "w", encoding="utf-8") as f:
        f.write("hello world")

    with open(p / ".spack" / "binary_distribution", "w", encoding="utf-8") as f:
        f.write("{}")

    os.symlink("app", str(relative_app_link))
    os.symlink(str(app), str(absolute_app_link))

    return str(p)


@pytest.mark.maybeslow
def test_buildcache_cmd_smoke_test(tmp_path: pathlib.Path, install_mockery):
    """
    Test the creation and installation of buildcaches with default rpaths
    into the default directory layout scheme.
    """
    mirror_cmd("add", "--type", "binary", "--unsigned", "test-mirror", str(tmp_path))

    # Install 'corge' without using a cache
    install_cmd("--fake", "--no-cache", "corge")
    install_cmd("--fake", "--no-cache", "symly")

    # Create a buildache
    buildcache_cmd("push", "-u", str(tmp_path), "corge", "symly")
    # Test force overwrite create buildcache (-f option)
    buildcache_cmd("push", "-uf", str(tmp_path), "corge")

    # Create mirror index
    buildcache_cmd("update-index", str(tmp_path))

    # List the buildcaches in the mirror
    buildcache_cmd("list", "-alv")

    # Uninstall the package and deps
    uninstall_cmd("-y", "--dependents", "garply")

    # Test installing from build caches
    buildcache_cmd("install", "-uo", "corge", "symly")

    # This gives warning that spec is already installed
    buildcache_cmd("install", "-uo", "corge")

    # Test overwrite install
    buildcache_cmd("install", "-ufo", "corge")

    buildcache_cmd("keys", "-f")
    buildcache_cmd("list")

    buildcache_cmd("list", "-a")
    buildcache_cmd("list", "-l", "-v")


def test_push_and_fetch_keys(mock_gnupghome, tmp_path: pathlib.Path):
    testpath = str(mock_gnupghome)

    mirror = os.path.join(testpath, "mirror")
    mirrors = {"test-mirror": url_util.path_to_file_url(mirror)}
    mirrors = spack.mirrors.mirror.MirrorCollection(mirrors)
    mirror = spack.mirrors.mirror.Mirror(url_util.path_to_file_url(mirror))

    gpg_dir1 = os.path.join(testpath, "gpg1")
    gpg_dir2 = os.path.join(testpath, "gpg2")

    # dir 1: create a new key, record its fingerprint, and push it to a new
    #        mirror
    with spack.util.gpg.gnupghome_override(gpg_dir1):
        spack.util.gpg.create(name="test-key", email="fake@test.key", expires="0", comment=None)

        keys = spack.util.gpg.public_keys()
        assert len(keys) == 1
        fpr = keys[0]

        spack.binary_distribution._url_push_keys(
            mirror, keys=[fpr], tmpdir=str(tmp_path), update_index=True
        )

    # dir 2: import the key from the mirror, and confirm that its fingerprint
    #        matches the one created above
    with spack.util.gpg.gnupghome_override(gpg_dir2):
        assert len(spack.util.gpg.public_keys()) == 0

        spack.binary_distribution.get_keys(mirrors=mirrors, install=True, trust=True, force=True)

        new_keys = spack.util.gpg.public_keys()
        assert len(new_keys) == 1
        assert new_keys[0] == fpr


@pytest.mark.maybeslow
def test_built_spec_cache(install_mockery, tmp_path: pathlib.Path):
    """Because the buildcache list command fetches the buildcache index
    and uses it to populate the binary_distribution built spec cache, when
    this test calls get_mirrors_for_spec, it is testing the popluation of
    that cache from a buildcache index."""

    install_cmd("--fake", "--no-cache", "corge")
    buildcache_cmd("push", "--unsigned", "--update-index", str(tmp_path), "corge")
    mirror_cmd("add", "--type", "binary", "--unsigned", "test-mirror", str(tmp_path))
    buildcache_cmd("list", "-a", "-l")

    gspec = spack.concretize.concretize_one("garply")
    cspec = spack.concretize.concretize_one("corge")

    for s in [gspec, cspec]:
        results = spack.binary_distribution.get_mirrors_for_spec(s)
        assert any([r.spec == s for r in results])


def fake_dag_hash(spec, length=None):
    # Generate an arbitrary hash that is intended to be different than
    # whatever a Spec reported before (to test actions that trigger when
    # the hash changes)
    return "tal4c7h4z0gqmixb1eqa92mjoybxn5l6"[:length]


@pytest.mark.usefixtures("install_mockery", "mock_packages", "mock_fetch", "temporary_mirror")
def test_spec_needs_rebuild(monkeypatch, tmp_path: pathlib.Path):
    """Make sure needs_rebuild properly compares remote hash
    against locally computed one, avoiding unnecessary rebuilds"""

    # Create a temp mirror directory for buildcache usage
    mirror_dir = tmp_path / "mirror_dir"
    mirror_url = url_util.path_to_file_url(str(mirror_dir))

    s = spack.concretize.concretize_one("libdwarf")

    # Install a package
    install_cmd("--fake", s.name)

    # Put installed package in the buildcache
    buildcache_cmd("push", "-u", str(mirror_dir), s.name)

    rebuild = spack.binary_distribution.needs_rebuild(s, mirror_url)

    assert not rebuild

    # Now monkey patch Spec to change the hash on the package
    monkeypatch.setattr(spack.spec.Spec, "dag_hash", fake_dag_hash)

    rebuild = spack.binary_distribution.needs_rebuild(s, mirror_url)

    assert rebuild


@pytest.mark.usefixtures("install_mockery", "mock_packages", "mock_fetch")
def test_generate_index_missing(monkeypatch, tmp_path: pathlib.Path, mutable_config):
    """Ensure spack buildcache index only reports available packages"""

    # Create a temp mirror directory for buildcache usage
    mirror_dir = tmp_path / "mirror_dir"
    mirror_url = url_util.path_to_file_url(str(mirror_dir))
    spack.config.set("mirrors", {"test": mirror_url})

    s = spack.concretize.concretize_one("libdwarf")

    # Install a package
    install_cmd("--fake", "--no-cache", s.name)

    # Create a buildcache and update index
    buildcache_cmd("push", "-u", str(mirror_dir), s.name)
    buildcache_cmd("update-index", str(mirror_dir))

    # Check package and dependency in buildcache
    cache_list = buildcache_cmd("list", "--allarch")
    assert "libdwarf" in cache_list
    assert "libelf" in cache_list

    # Remove dependency from cache
    libelf_files = glob.glob(
        os.path.join(
            str(mirror_dir / spack.binary_distribution.buildcache_relative_specs_path()),
            "libelf",
            "*libelf*",
        )
    )
    os.remove(*libelf_files)

    # Update index
    buildcache_cmd("update-index", str(mirror_dir))

    with spack.config.override("config:binary_index_ttl", 0):
        # Check dependency not in buildcache
        cache_list = buildcache_cmd("list", "--allarch")
        assert "libdwarf" in cache_list
        assert "libelf" not in cache_list


@pytest.mark.usefixtures("install_mockery", "mock_packages", "mock_fetch")
def test_use_bin_index(monkeypatch, tmp_path: pathlib.Path, mutable_config):
    """Check use of binary cache index: perform an operation that
    instantiates it, and a second operation that reconstructs it.
    """
    monkeypatch.setattr(
        spack.binary_distribution, "BINARY_INDEX", spack.binary_distribution.BinaryCacheIndex()
    )

    # Create a mirror, configure us to point at it, install a spec, and
    # put it in the mirror
    mirror_dir = tmp_path / "mirror_dir"
    mirror_url = url_util.path_to_file_url(str(mirror_dir))
    spack.config.set("mirrors", {"test": mirror_url})
    s = spack.concretize.concretize_one("libdwarf")
    install_cmd("--fake", "--no-cache", s.name)
    buildcache_cmd("push", "-u", str(mirror_dir), s.name)
    buildcache_cmd("update-index", str(mirror_dir))

    # Now the test
    buildcache_cmd("list", "-al")
    spack.binary_distribution.BINARY_INDEX = spack.binary_distribution.BinaryCacheIndex()
    cache_list = buildcache_cmd("list", "-al")
    assert "libdwarf" in cache_list


def test_generate_key_index_failure(monkeypatch, tmp_path: pathlib.Path):
    def list_url(url, recursive=False):
        if "fails-listing" in url:
            raise Exception("Couldn't list the directory")
        return ["first.pub", "second.pub"]

    def push_to_url(*args, **kwargs):
        raise Exception("Couldn't upload the file")

    monkeypatch.setattr(web_util, "list_url", list_url)
    monkeypatch.setattr(web_util, "push_to_url", push_to_url)

    with pytest.raises(CannotListKeys, match="Encountered problem listing keys"):
        spack.binary_distribution.generate_key_index(
            "s3://non-existent/fails-listing", str(tmp_path)
        )

    with pytest.raises(GenerateIndexError, match="problem pushing .* Couldn't upload"):
        spack.binary_distribution.generate_key_index(
            "s3://non-existent/fails-uploading", str(tmp_path)
        )


def test_generate_package_index_failure(monkeypatch, tmp_path: pathlib.Path, capfd):
    def mock_list_url(url, recursive=False):
        raise Exception("Some HTTP error")

    monkeypatch.setattr(web_util, "list_url", mock_list_url)

    test_url = "file:///fake/keys/dir"

    with pytest.raises(GenerateIndexError, match="Unable to generate package index"):
        spack.binary_distribution._url_generate_package_index(test_url, str(tmp_path))

    assert (
        "Warning: Encountered problem listing packages at "
        f"{test_url}: Some HTTP error" in capfd.readouterr().err
    )


def test_generate_indices_exception(monkeypatch, tmp_path: pathlib.Path, capfd):
    def mock_list_url(url, recursive=False):
        raise Exception("Test Exception handling")

    monkeypatch.setattr(web_util, "list_url", mock_list_url)

    url = "file:///fake/keys/dir"

    with pytest.raises(GenerateIndexError, match=f"Encountered problem listing keys at {url}"):
        spack.binary_distribution.generate_key_index(url, str(tmp_path))

    with pytest.raises(GenerateIndexError, match="Unable to generate package index"):
        spack.binary_distribution._url_generate_package_index(url, str(tmp_path))

    assert f"Encountered problem listing packages at {url}" in capfd.readouterr().err


def test_update_sbang(tmp_path: pathlib.Path, temporary_mirror, mock_fetch, install_mockery):
    """Test relocation of the sbang shebang line in a package script"""
    s = spack.concretize.concretize_one("old-sbang")
    PackageInstaller([s.package]).install()
    old_prefix, old_sbang_shebang = s.prefix, sbang.sbang_shebang_line()
    old_contents = f"""\
{old_sbang_shebang}
#!/usr/bin/env python3

{s.prefix.bin}
"""
    with open(os.path.join(s.prefix.bin, "script.sh"), encoding="utf-8") as f:
        assert f.read() == old_contents

    # Create a buildcache with the installed spec.
    buildcache_cmd("push", "--update-index", "--unsigned", temporary_mirror, f"/{s.dag_hash()}")

    # Switch the store to the new install tree locations
    with spack.store.use_store(str(tmp_path)):
        s._prefix = None  # clear the cached old prefix
        new_prefix, new_sbang_shebang = s.prefix, sbang.sbang_shebang_line()
        assert old_prefix != new_prefix
        assert old_sbang_shebang != new_sbang_shebang
        PackageInstaller([s.package], cache_only=True, unsigned=True).install()

        # Check that the sbang line refers to the new install tree
        new_contents = f"""\
{sbang.sbang_shebang_line()}
#!/usr/bin/env python3

{s.prefix.bin}
"""
        with open(os.path.join(s.prefix.bin, "script.sh"), encoding="utf-8") as f:
            assert f.read() == new_contents


def test_FetchCacheError_only_accepts_lists_of_errors():
    with pytest.raises(TypeError, match="list"):
        spack.binary_distribution.FetchCacheError("error")


def test_FetchCacheError_pretty_printing_multiple():
    e = spack.binary_distribution.FetchCacheError([RuntimeError("Oops!"), TypeError("Trouble!")])
    str_e = str(e)
    assert "Multiple errors" in str_e
    assert "Error 1: RuntimeError: Oops!" in str_e
    assert "Error 2: TypeError: Trouble!" in str_e
    assert str_e.rstrip() == str_e


def test_FetchCacheError_pretty_printing_single():
    e = spack.binary_distribution.FetchCacheError([RuntimeError("Oops!")])
    str_e = str(e)
    assert "Multiple errors" not in str_e
    assert "RuntimeError: Oops!" in str_e
    assert str_e.rstrip() == str_e


def test_text_relocate_if_needed(
    install_mockery, temporary_store, mock_fetch, tmp_path: pathlib.Path
):
    install_cmd("needs-text-relocation")
    spec = temporary_store.db.query_one("needs-text-relocation")
    tgz_path = tmp_path / "relocatable.tar.gz"
    spack.binary_distribution.create_tarball(spec, str(tgz_path))

    # extract the .spack/binary_distribution file
    with tarfile.open(tgz_path) as tar:
        entry_name = next(x for x in tar.getnames() if x.endswith(".spack/binary_distribution"))
        bd_file = tar.extractfile(entry_name)
        manifest = syaml.load(bd_file)

    assert join_path("bin", "exe") in manifest["relocate_textfiles"]
    assert join_path("bin", "otherexe") not in manifest["relocate_textfiles"]
    assert join_path("bin", "secretexe") not in manifest["relocate_textfiles"]


def test_compression_writer(tmp_path: pathlib.Path):
    text = "This is some text. We might or might not like to compress it as we write."
    checksum_algo = "sha256"

    # Write the data using gzip compression
    compressed_output_path = str(tmp_path / "compressed_text")
    with compression_writer(compressed_output_path, "gzip", checksum_algo) as (
        compressor,
        checker,
    ):
        compressor.write(text.encode("utf-8"))

    compressed_size = checker.length
    compressed_checksum = checker.hexdigest()

    with open(compressed_output_path, "rb") as f:
        binary_content = f.read()

    assert spack.binary_distribution.compute_hash(binary_content) == compressed_checksum
    assert os.stat(compressed_output_path).st_size == compressed_size
    assert binary_content[:2] == b"\x1f\x8b"
    decompressed_content = gzip.decompress(binary_content).decode("utf-8")

    assert decompressed_content == text

    # Write the data without compression
    uncompressed_output_path = str(tmp_path / "uncompressed_text")
    with compression_writer(uncompressed_output_path, "none", checksum_algo) as (
        compressor,
        checker,
    ):
        compressor.write(text.encode("utf-8"))

    uncompressed_size = checker.length
    uncompressed_checksum = checker.hexdigest()

    with open(uncompressed_output_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert spack.binary_distribution.compute_hash(content) == uncompressed_checksum
    assert os.stat(uncompressed_output_path).st_size == uncompressed_size
    assert content == text

    # Make sure we raise if requesting unknown compression type
    nocare_output_path = str(tmp_path / "wontwrite")
    with pytest.raises(BuildcacheEntryError, match="Unknown compression type"):
        with compression_writer(nocare_output_path, "gsip", checksum_algo) as (
            compressor,
            checker,
        ):
            compressor.write(text)


def test_v2_etag_fetching_304():
    # Test conditional fetch with etags. If the remote hasn't modified the file
    # it returns 304, which is an HTTPError in urllib-land. That should be
    # handled as success, since it means the local cache is up-to-date.
    def response_304(request: urllib.request.Request):
        url = request.get_full_url()
        if url == f"https://www.example.com/build_cache/{INDEX_JSON_FILE}":
            assert request.get_header("If-none-match") == '"112a8bbc1b3f7f185621c1ee335f0502"'
            raise urllib.error.HTTPError(
                url, 304, "Not Modified", hdrs={}, fp=None  # type: ignore[arg-type]
            )
        assert False, "Should not fetch {}".format(url)

    fetcher = spack.binary_distribution.EtagIndexFetcherV2(
        url="https://www.example.com",
        etag="112a8bbc1b3f7f185621c1ee335f0502",
        urlopen=response_304,
    )

    result = fetcher.conditional_fetch()
    assert isinstance(result, spack.binary_distribution.FetchIndexResult)
    assert result.fresh


def test_v2_etag_fetching_200():
    # Test conditional fetch with etags. The remote has modified the file.
    def response_200(request: urllib.request.Request):
        url = request.get_full_url()
        if url == f"https://www.example.com/build_cache/{INDEX_JSON_FILE}":
            assert request.get_header("If-none-match") == '"112a8bbc1b3f7f185621c1ee335f0502"'
            return urllib.response.addinfourl(
                io.BytesIO(b"Result"),
                headers={"Etag": '"59bcc3ad6775562f845953cf01624225"'},  # type: ignore[arg-type]
                url=url,
                code=200,
            )
        assert False, "Should not fetch {}".format(url)

    fetcher = spack.binary_distribution.EtagIndexFetcherV2(
        url="https://www.example.com",
        etag="112a8bbc1b3f7f185621c1ee335f0502",
        urlopen=response_200,
    )

    result = fetcher.conditional_fetch()
    assert isinstance(result, spack.binary_distribution.FetchIndexResult)
    assert not result.fresh
    assert result.etag == "59bcc3ad6775562f845953cf01624225"
    assert result.data == "Result"  # decoded utf-8.
    assert result.hash == spack.binary_distribution.compute_hash("Result")


def test_v2_etag_fetching_404():
    # Test conditional fetch with etags. The remote has modified the file.
    def response_404(request: urllib.request.Request):
        raise urllib.error.HTTPError(
            request.get_full_url(),
            404,
            "Not found",
            hdrs={"Etag": '"59bcc3ad6775562f845953cf01624225"'},  # type: ignore[arg-type]
            fp=None,
        )

    fetcher = spack.binary_distribution.EtagIndexFetcherV2(
        url="https://www.example.com",
        etag="112a8bbc1b3f7f185621c1ee335f0502",
        urlopen=response_404,
    )

    with pytest.raises(spack.binary_distribution.FetchIndexError):
        fetcher.conditional_fetch()


def test_v2_default_index_fetch_200():
    index_json = '{"Hello": "World"}'
    index_json_hash = spack.binary_distribution.compute_hash(index_json)

    def urlopen(request: urllib.request.Request):
        url = request.get_full_url()
        if url.endswith("index.json.hash"):
            return urllib.response.addinfourl(  # type: ignore[arg-type]
                io.BytesIO(index_json_hash.encode()),
                headers={},  # type: ignore[arg-type]
                url=url,
                code=200,
            )

        elif url.endswith(INDEX_JSON_FILE):
            return urllib.response.addinfourl(
                io.BytesIO(index_json.encode()),
                headers={"Etag": '"59bcc3ad6775562f845953cf01624225"'},  # type: ignore[arg-type]
                url=url,
                code=200,
            )

        assert False, "Unexpected request {}".format(url)

    fetcher = spack.binary_distribution.DefaultIndexFetcherV2(
        url="https://www.example.com", local_hash="outdated", urlopen=urlopen
    )

    result = fetcher.conditional_fetch()

    assert isinstance(result, spack.binary_distribution.FetchIndexResult)
    assert not result.fresh
    assert result.etag == "59bcc3ad6775562f845953cf01624225"
    assert result.data == index_json
    assert result.hash == index_json_hash


def test_v2_default_index_dont_fetch_index_json_hash_if_no_local_hash():
    # When we don't have local hash, we should not be fetching the
    # remote index.json.hash file, but only index.json.
    index_json = '{"Hello": "World"}'
    index_json_hash = spack.binary_distribution.compute_hash(index_json)

    def urlopen(request: urllib.request.Request):
        url = request.get_full_url()
        if url.endswith(INDEX_JSON_FILE):
            return urllib.response.addinfourl(
                io.BytesIO(index_json.encode()),
                headers={"Etag": '"59bcc3ad6775562f845953cf01624225"'},  # type: ignore[arg-type]
                url=url,
                code=200,
            )

        assert False, "Unexpected request {}".format(url)

    fetcher = spack.binary_distribution.DefaultIndexFetcherV2(
        url="https://www.example.com", local_hash=None, urlopen=urlopen
    )

    result = fetcher.conditional_fetch()

    assert isinstance(result, spack.binary_distribution.FetchIndexResult)
    assert result.data == index_json
    assert result.hash == index_json_hash
    assert result.etag == "59bcc3ad6775562f845953cf01624225"
    assert not result.fresh


def test_v2_default_index_not_modified():
    index_json = '{"Hello": "World"}'
    index_json_hash = spack.binary_distribution.compute_hash(index_json)

    def urlopen(request: urllib.request.Request):
        url = request.get_full_url()
        if url.endswith("index.json.hash"):
            return urllib.response.addinfourl(
                io.BytesIO(index_json_hash.encode()),
                headers={},  # type: ignore[arg-type]
                url=url,
                code=200,
            )

        # No request to index.json should be made.
        assert False, "Unexpected request {}".format(url)

    fetcher = spack.binary_distribution.DefaultIndexFetcherV2(
        url="https://www.example.com", local_hash=index_json_hash, urlopen=urlopen
    )

    assert fetcher.conditional_fetch().fresh


@pytest.mark.parametrize("index_json", [b"\xa9", b"!#%^"])
def test_v2_default_index_invalid_hash_file(index_json):
    # Test invalid unicode / invalid hash type
    index_json_hash = spack.binary_distribution.compute_hash(index_json)

    def urlopen(request: urllib.request.Request):
        return urllib.response.addinfourl(
            io.BytesIO(),
            headers={},  # type: ignore[arg-type]
            url=request.get_full_url(),
            code=200,
        )

    fetcher = spack.binary_distribution.DefaultIndexFetcherV2(
        url="https://www.example.com", local_hash=index_json_hash, urlopen=urlopen
    )

    assert fetcher.get_remote_hash() is None


def test_v2_default_index_json_404():
    # Test invalid unicode / invalid hash type
    index_json = '{"Hello": "World"}'
    index_json_hash = spack.binary_distribution.compute_hash(index_json)

    def urlopen(request: urllib.request.Request):
        url = request.get_full_url()
        if url.endswith("index.json.hash"):
            return urllib.response.addinfourl(
                io.BytesIO(index_json_hash.encode()),
                headers={},  # type: ignore[arg-type]
                url=url,
                code=200,
            )

        elif url.endswith(INDEX_JSON_FILE):
            raise urllib.error.HTTPError(
                url,
                code=404,
                msg="Not Found",
                hdrs={"Etag": '"59bcc3ad6775562f845953cf01624225"'},  # type: ignore[arg-type]
                fp=None,
            )

        assert False, "Unexpected fetch {}".format(url)

    fetcher = spack.binary_distribution.DefaultIndexFetcherV2(
        url="https://www.example.com", local_hash="invalid", urlopen=urlopen
    )

    with pytest.raises(spack.binary_distribution.FetchIndexError, match="Could not fetch index"):
        fetcher.conditional_fetch()


def _all_parents(prefix):
    parts = [p for p in prefix.split("/")]
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


def test_tarball_doesnt_include_buildinfo_twice(tmp_path: Path):
    """When tarballing a package that was installed from a buildcache, make
    sure that the buildinfo file is not included twice in the tarball."""
    p = tmp_path / "prefix"
    p.joinpath(".spack").mkdir(parents=True)

    # Create a binary_distribution file in the .spack folder
    with open(p / ".spack" / "binary_distribution", "w", encoding="utf-8") as f:
        f.write(syaml.dump({"metadata", "old"}))

    # Now create a tarball, which should include a new binary_distribution file
    tarball = str(tmp_path / "prefix.tar.gz")

    spack.binary_distribution._do_create_tarball(
        tarfile_path=tarball, prefix=str(p), buildinfo={"metadata": "new"}, prefixes_to_relocate=[]
    )

    expected_prefix = str(p).lstrip("/")

    # Verify we don't have a repeated binary_distribution file,
    # and that the tarball contains the new one, not the old one.
    with tarfile.open(tarball) as tar:
        assert syaml.load(tar.extractfile(f"{expected_prefix}/.spack/binary_distribution")) == {
            "metadata": "new",
            "relocate_binaries": [],
            "relocate_textfiles": [],
            "relocate_links": [],
        }
        assert tar.getnames() == [
            *_all_parents(expected_prefix),
            f"{expected_prefix}/.spack",
            f"{expected_prefix}/.spack/binary_distribution",
        ]


def test_reproducible_tarball_is_reproducible(tmp_path: Path):
    p = tmp_path / "prefix"
    p.joinpath("bin").mkdir(parents=True)
    p.joinpath(".spack").mkdir(parents=True)
    app = p / "bin" / "app"

    tarball_1 = str(tmp_path / "prefix-1.tar.gz")
    tarball_2 = str(tmp_path / "prefix-2.tar.gz")

    with open(app, "w", encoding="utf-8") as f:
        f.write("hello world")

    buildinfo = {"metadata": "yes please"}

    # Create a tarball with a certain mtime of bin/app
    os.utime(app, times=(0, 0))
    spack.binary_distribution._do_create_tarball(
        tarball_1, prefix=str(p), buildinfo=buildinfo, prefixes_to_relocate=[]
    )

    # Do it another time with different mtime of bin/app
    os.utime(app, times=(10, 10))
    spack.binary_distribution._do_create_tarball(
        tarball_2, prefix=str(p), buildinfo=buildinfo, prefixes_to_relocate=[]
    )

    # They should be bitwise identical:
    assert filecmp.cmp(tarball_1, tarball_2, shallow=False)

    expected_prefix = str(p).lstrip("/")

    # Sanity check for contents:
    with tarfile.open(tarball_1, mode="r") as f:
        for m in f.getmembers():
            assert m.uid == m.gid == m.mtime == 0
            assert m.uname == m.gname == ""

        assert set(f.getnames()) == {
            *_all_parents(expected_prefix),
            f"{expected_prefix}/bin",
            f"{expected_prefix}/bin/app",
            f"{expected_prefix}/.spack",
            f"{expected_prefix}/.spack/binary_distribution",
        }


def test_tarball_normalized_permissions(tmp_path: pathlib.Path):
    p = tmp_path / "prefix"
    p.mkdir()
    (p / "bin").mkdir()
    (p / "share").mkdir()
    (p / ".spack").mkdir()

    app = p / "bin" / "app"
    data = p / "share" / "file"
    tarball = str(tmp_path / "prefix.tar.gz")

    # Everyone can write & execute. This should turn into 0o755 when the tarball is
    # extracted (on a different system).
    with open(
        app, "w", opener=lambda path, flags: os.open(path, flags, 0o777), encoding="utf-8"
    ) as f:
        f.write("hello world")

    # User doesn't have execute permissions, but group/world have; this should also
    # turn into 0o644 (user read/write, group&world only read).
    with open(
        data, "w", opener=lambda path, flags: os.open(path, flags, 0o477), encoding="utf-8"
    ) as f:
        f.write("hello world")

    spack.binary_distribution._do_create_tarball(
        tarball, prefix=str(p), buildinfo={}, prefixes_to_relocate=[]
    )

    expected_prefix = str(p).lstrip("/")

    with tarfile.open(tarball) as tar:
        path_to_member = {member.name: member for member in tar.getmembers()}

    # directories should have 0o755
    assert path_to_member[f"{expected_prefix}"].mode == 0o755
    assert path_to_member[f"{expected_prefix}/bin"].mode == 0o755
    assert path_to_member[f"{expected_prefix}/.spack"].mode == 0o755

    # executable-by-user files should be 0o755
    assert path_to_member[f"{expected_prefix}/bin/app"].mode == 0o755

    # not-executable-by-user files should be 0o644
    assert path_to_member[f"{expected_prefix}/share/file"].mode == 0o644


def test_tarball_common_prefix(dummy_prefix, tmp_path: pathlib.Path):
    """Tests whether Spack can figure out the package directory from the tarball contents, and
    strip them when extracting. This test creates a CURRENT_BUILD_CACHE_LAYOUT_VERSION=1 type
    tarball where the parent directories of the package prefix are missing. Spack should be able
    to figure out the common prefix and extract the files into the correct location."""

    # When creating a tarball, Python (and tar) use relative paths,
    # Absolute paths become relative to `/`, so drop the leading `/`.
    assert os.path.isabs(dummy_prefix)
    expected_prefix = PurePath(dummy_prefix).as_posix().lstrip("/")

    with working_dir(str(tmp_path)):
        # Create a tarball (using absolute path for prefix dir)
        with tarfile.open("example.tar", mode="w") as tar:
            tar.add(name=dummy_prefix)

        # Open, verify common prefix, and extract it.
        with tarfile.open("example.tar", mode="r") as tar:
            common_prefix = spack.binary_distribution._ensure_common_prefix(tar)
            assert common_prefix == expected_prefix

            # Extract into prefix2
            tar.extractall(
                path="prefix2",
                members=spack.binary_distribution._tar_strip_component(tar, common_prefix),
            )

        # Verify files are all there at the correct level.
        assert set(os.listdir("prefix2")) == {"bin", "share", ".spack"}
        assert set(os.listdir(os.path.join("prefix2", "bin"))) == {
            "app",
            "relative_app_link",
            "absolute_app_link",
        }
        assert set(os.listdir(os.path.join("prefix2", "share"))) == {"file"}

        # Relative symlink should still be correct
        assert readlink(os.path.join("prefix2", "bin", "relative_app_link")) == "app"

        # Absolute symlink should remain absolute -- this is for relocation to fix up.
        assert readlink(os.path.join("prefix2", "bin", "absolute_app_link")) == os.path.join(
            dummy_prefix, "bin", "app"
        )


def test_tarfile_missing_binary_distribution_file(tmp_path: pathlib.Path):
    """A tarfile that does not contain a .spack/binary_distribution file cannot be
    used to install."""
    with working_dir(str(tmp_path)):
        # An empty .spack dir.
        with tarfile.open("empty.tar", mode="w") as tar:
            tarinfo = tarfile.TarInfo(name="example/.spack")
            tarinfo.type = tarfile.DIRTYPE
            tar.addfile(tarinfo)

        with pytest.raises(ValueError, match="missing binary_distribution file"):
            spack.binary_distribution._ensure_common_prefix(tarfile.open("empty.tar", mode="r"))


def test_tarfile_without_common_directory_prefix_fails(tmp_path: pathlib.Path):
    """A tarfile that only contains files without a common package directory
    should fail to extract, as we won't know where to put the files."""
    with working_dir(str(tmp_path)):
        # Create a broken tarball with just a file, no directories.
        with tarfile.open("empty.tar", mode="w") as tar:
            tar.addfile(
                tarfile.TarInfo(name="example/.spack/binary_distribution"),
                fileobj=io.BytesIO(b"hello"),
            )

        with pytest.raises(ValueError, match="Tarball does not contain a common prefix"):
            spack.binary_distribution._ensure_common_prefix(tarfile.open("empty.tar", mode="r"))


def test_tarfile_with_files_outside_common_prefix(tmp_path: pathlib.Path, dummy_prefix):
    """If a file is outside of the common prefix, we should fail."""
    with working_dir(str(tmp_path)):
        with tarfile.open("broken.tar", mode="w") as tar:
            tar.add(name=dummy_prefix)
            tar.addfile(tarfile.TarInfo(name="/etc/config_file"), fileobj=io.BytesIO(b"hello"))

        with pytest.raises(
            ValueError, match="Tarball contains file /etc/config_file outside of prefix"
        ):
            spack.binary_distribution._ensure_common_prefix(tarfile.open("broken.tar", mode="r"))


def test_tarfile_of_spec_prefix(tmp_path: pathlib.Path):
    """Tests whether hardlinks, symlinks, files and dirs are added correctly,
    and that the order of entries is correct."""
    prefix = tmp_path / "prefix"
    prefix.mkdir()

    (prefix / "a_directory").mkdir()
    (prefix / "a_directory" / "file").write_text("hello")

    (prefix / "c_directory").mkdir()
    (prefix / "c_directory" / "file").write_text("hello")

    (prefix / "b_directory").mkdir()
    (prefix / "b_directory" / "file").write_text("hello")

    (prefix / "file").write_text("hello")
    os.symlink(str(prefix / "file"), str(prefix / "symlink"))
    os.link(str(prefix / "file"), str(prefix / "hardlink"))

    file = tmp_path / "example.tar"

    with tarfile.open(str(file), mode="w") as tar:
        spack.binary_distribution.tarfile_of_spec_prefix(tar, str(prefix), prefixes_to_relocate=[])

    expected_prefix = str(prefix).lstrip("/")

    with tarfile.open(str(file), mode="r") as tar:
        # Verify that entries are added in depth-first pre-order, files preceding dirs,
        # entries ordered alphabetically
        assert tar.getnames() == [
            *_all_parents(expected_prefix),
            f"{expected_prefix}/file",
            f"{expected_prefix}/hardlink",
            f"{expected_prefix}/symlink",
            f"{expected_prefix}/a_directory",
            f"{expected_prefix}/a_directory/file",
            f"{expected_prefix}/b_directory",
            f"{expected_prefix}/b_directory/file",
            f"{expected_prefix}/c_directory",
            f"{expected_prefix}/c_directory/file",
        ]

        # Check that the types are all correct
        assert tar.getmember(f"{expected_prefix}").isdir()
        assert tar.getmember(f"{expected_prefix}/file").isreg()
        assert tar.getmember(f"{expected_prefix}/hardlink").islnk()
        assert tar.getmember(f"{expected_prefix}/symlink").issym()
        assert tar.getmember(f"{expected_prefix}/a_directory").isdir()
        assert tar.getmember(f"{expected_prefix}/a_directory/file").isreg()
        assert tar.getmember(f"{expected_prefix}/b_directory").isdir()
        assert tar.getmember(f"{expected_prefix}/b_directory/file").isreg()
        assert tar.getmember(f"{expected_prefix}/c_directory").isdir()
        assert tar.getmember(f"{expected_prefix}/c_directory/file").isreg()


@pytest.mark.parametrize("layout,expect_success", [(None, True), (1, True), (2, False)])
def test_get_valid_spec_file(tmp_path: pathlib.Path, layout, expect_success):
    # Test reading a spec.json file that does not specify a layout version.
    spec_dict = Spec("example").to_dict()
    path = tmp_path / "spec.json"
    effective_layout = layout or 0  # If not specified it should be 0

    # Add a layout version
    if layout is not None:
        spec_dict["buildcache_layout_version"] = layout

    # Save to file
    with open(path, "w", encoding="utf-8") as f:
        json.dump(spec_dict, f)

    try:
        spec_dict_disk, layout_disk = get_valid_spec_file(str(path), max_supported_layout=1)
        assert expect_success
        assert spec_dict_disk == spec_dict
        assert layout_disk == effective_layout
    except spack.binary_distribution.InvalidMetadataFile:
        assert not expect_success


def test_get_valid_spec_file_doesnt_exist(tmp_path: pathlib.Path):
    with pytest.raises(spack.binary_distribution.InvalidMetadataFile, match="No such file"):
        get_valid_spec_file(str(tmp_path / "no-such-file"), max_supported_layout=1)


@pytest.mark.parametrize("filename", ["spec.json", "spec.json.sig"])
def test_get_valid_spec_file_no_json(tmp_path: pathlib.Path, filename):
    tmp_path.joinpath(filename).write_text("not json")
    with pytest.raises(spack.binary_distribution.InvalidMetadataFile):
        get_valid_spec_file(str(tmp_path / filename), max_supported_layout=1)


@pytest.mark.usefixtures("install_mockery", "mock_packages", "mock_fetch", "temporary_mirror")
def test_url_buildcache_entry_v3(monkeypatch, tmp_path: pathlib.Path):
    """Make sure URLBuildcacheEntry behaves as expected"""

    # Create a temp mirror directory for buildcache usage
    mirror_dir = tmp_path / "mirror_dir"
    mirror_url = url_util.path_to_file_url(str(mirror_dir))

    s = spack.concretize.concretize_one("libdwarf")

    # Install libdwarf
    install_cmd("--fake", s.name)

    # Push libdwarf to buildcache
    buildcache_cmd("push", "-u", str(mirror_dir), s.name)

    cache_class = get_url_buildcache_class(
        spack.binary_distribution.CURRENT_BUILD_CACHE_LAYOUT_VERSION
    )
    build_cache = cache_class(mirror_url, s, allow_unsigned=True)

    manifest = build_cache.read_manifest()
    spec_dict = build_cache.fetch_metadata()
    local_tarball_path = build_cache.fetch_archive()

    assert "spec" in spec_dict

    for blob_record in manifest.data:
        blob_path = build_cache.get_staged_blob_path(blob_record)
        assert os.path.exists(blob_path)
        actual_blob_size = os.stat(blob_path).st_size
        assert blob_record.content_length == actual_blob_size

    build_cache.destroy()

    assert not os.path.exists(local_tarball_path)


def test_relative_path_components():
    blobs_v3 = URLBuildcacheEntry.get_relative_path_components(BuildcacheComponent.BLOB)
    assert len(blobs_v3) == 1
    assert "blobs" in blobs_v3

    blobs_v2 = URLBuildcacheEntryV2.get_relative_path_components(BuildcacheComponent.BLOB)
    assert len(blobs_v2) == 1
    assert "build_cache" in blobs_v2

    v2_spec_url = "file:///home/me/mymirror/build_cache/linux-ubuntu22.04-sapphirerapids-gcc-12.3.0-gmake-4.4.1-5pddli3htvfe6svs7nbrqmwi5735agi3.spec.json.sig"
    assert URLBuildcacheEntryV2.get_base_url(v2_spec_url) == "file:///home/me/mymirror"

    v3_manifest_url = "file:///home/me/mymirror/v3/manifests/gmake-4.4.1-5pddli3htvfe6svs7nbrqmwi5735agi3.spec.manifest.json"
    assert URLBuildcacheEntry.get_base_url(v3_manifest_url) == "file:///home/me/mymirror"


@pytest.mark.parametrize(
    "spec",
    [
        # Standard case
        "short-name@=1.2.3",
        # Unsupported characters in git version
        f"git-version@{1:040x}=develop",
        # Too long of a name
        f"{'too-long':x<256}@=1.2.3",
    ],
)
def test_default_tag(spec: str):
    """Make sure that computed image tags are valid."""
    assert re.fullmatch(
        spack.oci.image.tag, spack.binary_distribution._oci_default_tag(spack.spec.Spec(spec))
    )


class IndexInformation(NamedTuple):
    manifest_contents: Dict[str, Any]
    index_contents: str
    index_hash: str
    manifest_path: str
    index_path: str
    manifest_etag: str
    fetched_blob: Callable[[], bool]


@pytest.fixture
def mock_index(tmp_path: pathlib.Path, monkeypatch) -> IndexInformation:
    mirror_root = tmp_path / "mymirror"
    index_json = '{"Hello": "World"}'
    index_json_hash = spack.binary_distribution.compute_hash(index_json)
    fetched = False

    cache_class = get_url_buildcache_class(
        layout_version=spack.binary_distribution.CURRENT_BUILD_CACHE_LAYOUT_VERSION
    )

    index_blob_path = os.path.join(
        str(mirror_root),
        *cache_class.get_relative_path_components(BuildcacheComponent.BLOB),
        "sha256",
        index_json_hash[:2],
        index_json_hash,
    )

    os.makedirs(os.path.dirname(index_blob_path))
    with open(index_blob_path, "w", encoding="utf-8") as fd:
        fd.write(index_json)

    index_blob_record = spack.binary_distribution.BlobRecord(
        os.stat(index_blob_path).st_size,
        cache_class.BUILDCACHE_INDEX_MEDIATYPE,
        "none",
        "sha256",
        index_json_hash,
    )

    index_manifest = {
        "version": cache_class.get_layout_version(),
        "data": [index_blob_record.to_dict()],
    }

    manifest_json_path = cache_class.get_index_url(str(mirror_root))

    os.makedirs(os.path.dirname(manifest_json_path))

    with open(manifest_json_path, "w", encoding="utf-8") as f:
        json.dump(index_manifest, f)

    def fetch_patch(stage, mirror_only: bool = False, err_msg: Optional[str] = None):
        nonlocal fetched
        fetched = True

    @property  # type: ignore
    def save_filename_patch(stage):
        return str(index_blob_path)

    monkeypatch.setattr(spack.stage.Stage, "fetch", fetch_patch)
    monkeypatch.setattr(spack.stage.Stage, "save_filename", save_filename_patch)

    def get_did_fetch():
        # nonlocal fetched
        return fetched

    return IndexInformation(
        index_manifest,
        index_json,
        index_json_hash,
        manifest_json_path,
        index_blob_path,
        "59bcc3ad6775562f845953cf01624225",
        get_did_fetch,
    )


def test_etag_fetching_304():
    # Test conditional fetch with etags. If the remote hasn't modified the file
    # it returns 304, which is an HTTPError in urllib-land. That should be
    # handled as success, since it means the local cache is up-to-date.
    def response_304(request: urllib.request.Request):
        url = request.get_full_url()
        if url.endswith(INDEX_MANIFEST_FILE):
            assert request.get_header("If-none-match") == '"112a8bbc1b3f7f185621c1ee335f0502"'
            raise urllib.error.HTTPError(
                url, 304, "Not Modified", hdrs={}, fp=None  # type: ignore[arg-type]
            )
        assert False, "Unexpected request {}".format(url)

    fetcher = spack.binary_distribution.EtagIndexFetcher(
        spack.binary_distribution.MirrorURLAndVersion(
            "https://www.example.com", spack.binary_distribution.CURRENT_BUILD_CACHE_LAYOUT_VERSION
        ),
        etag="112a8bbc1b3f7f185621c1ee335f0502",
        urlopen=response_304,
    )

    result = fetcher.conditional_fetch()
    assert isinstance(result, spack.binary_distribution.FetchIndexResult)
    assert result.fresh


def test_etag_fetching_200(mock_index):
    # Test conditional fetch with etags. The remote has modified the file.
    def response_200(request: urllib.request.Request):
        url = request.get_full_url()
        if url.endswith(INDEX_MANIFEST_FILE):
            assert request.get_header("If-none-match") == '"112a8bbc1b3f7f185621c1ee335f0502"'
            return urllib.response.addinfourl(
                io.BytesIO(json.dumps(mock_index.manifest_contents).encode()),
                headers={"Etag": f'"{mock_index.manifest_etag}"'},  # type: ignore[arg-type]
                url=url,
                code=200,
            )
        assert False, "Unexpected request {}".format(url)

    fetcher = spack.binary_distribution.EtagIndexFetcher(
        spack.binary_distribution.MirrorURLAndVersion(
            "https://www.example.com", spack.binary_distribution.CURRENT_BUILD_CACHE_LAYOUT_VERSION
        ),
        etag="112a8bbc1b3f7f185621c1ee335f0502",
        urlopen=response_200,
    )

    result = fetcher.conditional_fetch()
    assert isinstance(result, spack.binary_distribution.FetchIndexResult)
    assert not result.fresh
    assert mock_index.fetched_blob()
    assert result.etag == mock_index.manifest_etag
    assert result.data == mock_index.index_contents
    assert result.hash == mock_index.index_hash


def test_etag_fetching_404():
    # Test conditional fetch with etags. The remote has modified the file.
    def response_404(request: urllib.request.Request):
        raise urllib.error.HTTPError(
            request.get_full_url(),
            404,
            "Not found",
            hdrs={"Etag": '"59bcc3ad6775562f845953cf01624225"'},  # type: ignore[arg-type]
            fp=None,
        )

    fetcher = spack.binary_distribution.EtagIndexFetcher(
        spack.binary_distribution.MirrorURLAndVersion(
            "https://www.example.com", spack.binary_distribution.CURRENT_BUILD_CACHE_LAYOUT_VERSION
        ),
        etag="112a8bbc1b3f7f185621c1ee335f0502",
        urlopen=response_404,
    )

    with pytest.raises(spack.binary_distribution.FetchIndexError):
        fetcher.conditional_fetch()


def test_default_index_fetch_200(mock_index):
    # We fetch the manifest and then the index blob if the hash is outdated
    def urlopen(request: urllib.request.Request):
        url = request.get_full_url()
        if url.endswith(INDEX_MANIFEST_FILE):
            return urllib.response.addinfourl(  # type: ignore[arg-type]
                io.BytesIO(json.dumps(mock_index.manifest_contents).encode()),
                headers={"Etag": f'"{mock_index.manifest_etag}"'},  # type: ignore[arg-type]
                url=url,
                code=200,
            )

        assert False, "Unexpected request {}".format(url)

    fetcher = spack.binary_distribution.DefaultIndexFetcher(
        spack.binary_distribution.MirrorURLAndVersion(
            "https://www.example.com", spack.binary_distribution.CURRENT_BUILD_CACHE_LAYOUT_VERSION
        ),
        local_hash="outdated",
        urlopen=urlopen,
    )

    result = fetcher.conditional_fetch()

    assert isinstance(result, spack.binary_distribution.FetchIndexResult)
    assert not result.fresh
    assert mock_index.fetched_blob()
    assert result.etag == mock_index.manifest_etag
    assert result.data == mock_index.index_contents
    assert result.hash == mock_index.index_hash


def test_default_index_404():
    # We get a fetch error if the index can't be fetched
    def urlopen(request: urllib.request.Request):
        raise urllib.error.HTTPError(
            request.get_full_url(),
            404,
            "Not found",
            hdrs={"Etag": '"59bcc3ad6775562f845953cf01624225"'},  # type: ignore[arg-type]
            fp=None,
        )

    fetcher = spack.binary_distribution.DefaultIndexFetcher(
        spack.binary_distribution.MirrorURLAndVersion(
            "https://www.example.com", spack.binary_distribution.CURRENT_BUILD_CACHE_LAYOUT_VERSION
        ),
        local_hash=None,
        urlopen=urlopen,
    )

    with pytest.raises(spack.binary_distribution.FetchIndexError):
        fetcher.conditional_fetch()


def test_default_index_not_modified(mock_index):
    # We don't fetch the index blob if hash didn't change
    def urlopen(request: urllib.request.Request):
        url = request.get_full_url()
        if url.endswith(INDEX_MANIFEST_FILE):
            return urllib.response.addinfourl(
                io.BytesIO(json.dumps(mock_index.manifest_contents).encode()),
                headers={},  # type: ignore[arg-type]
                url=url,
                code=200,
            )

        # No other request should be made.
        assert False, "Unexpected request {}".format(url)

    fetcher = spack.binary_distribution.DefaultIndexFetcher(
        spack.binary_distribution.MirrorURLAndVersion(
            "https://www.example.com", spack.binary_distribution.CURRENT_BUILD_CACHE_LAYOUT_VERSION
        ),
        local_hash=mock_index.index_hash,
        urlopen=urlopen,
    )

    assert fetcher.conditional_fetch().fresh
    assert not mock_index.fetched_blob()
