# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)
import os
import pathlib

import pytest

import spack
import spack.environment
import spack.package_base
import spack.paths
import spack.repo
import spack.schema.repos
import spack.spec
import spack.util.executable
import spack.util.file_cache
import spack.util.lock
import spack.util.naming
from spack.util.naming import valid_module_name


@pytest.fixture(params=["packages", "", "foo"])
def extra_repo(tmp_path_factory, request):
    repo_namespace = "extra_test_repo"
    repo_dir = tmp_path_factory.mktemp(repo_namespace)
    cache_dir = tmp_path_factory.mktemp("cache")
    (repo_dir / request.param).mkdir(parents=True, exist_ok=True)
    if request.param == "packages":
        (repo_dir / "repo.yaml").write_text(
            """
repo:
  namespace: extra_test_repo
"""
        )
    else:
        (repo_dir / "repo.yaml").write_text(
            f"""
repo:
  namespace: extra_test_repo
  subdirectory: '{request.param}'
"""
        )
    repo_cache = spack.util.file_cache.FileCache(cache_dir)
    return spack.repo.Repo(str(repo_dir), cache=repo_cache), request.param


def test_repo_getpkg(mutable_mock_repo):
    mutable_mock_repo.get_pkg_class("pkg-a")
    mutable_mock_repo.get_pkg_class("builtin_mock.pkg-a")


def test_repo_multi_getpkg(mutable_mock_repo, extra_repo):
    mutable_mock_repo.put_first(extra_repo[0])
    mutable_mock_repo.get_pkg_class("pkg-a")
    mutable_mock_repo.get_pkg_class("builtin_mock.pkg-a")


def test_repo_multi_getpkgclass(mutable_mock_repo, extra_repo):
    mutable_mock_repo.put_first(extra_repo[0])
    mutable_mock_repo.get_pkg_class("pkg-a")
    mutable_mock_repo.get_pkg_class("builtin_mock.pkg-a")


def test_repo_pkg_with_unknown_namespace(mutable_mock_repo):
    with pytest.raises(spack.repo.UnknownNamespaceError):
        mutable_mock_repo.get_pkg_class("unknown.pkg-a")


def test_repo_unknown_pkg(mutable_mock_repo):
    with pytest.raises(spack.repo.UnknownPackageError):
        mutable_mock_repo.get_pkg_class("builtin_mock.nonexistentpackage")


def test_repo_last_mtime(mock_packages):
    mtime_with_package_py = [
        (os.path.getmtime(p.module.__file__), p.module.__file__)
        for p in spack.repo.PATH.all_package_classes()
    ]
    repo_mtime = spack.repo.PATH.last_mtime()
    max_mtime, max_file = max(mtime_with_package_py)
    if max_mtime > repo_mtime:
        modified_after = "\n    ".join(
            f"{path} ({mtime})" for mtime, path in mtime_with_package_py if mtime > repo_mtime
        )
        assert (
            max_mtime <= repo_mtime
        ), f"the following files were modified while running tests:\n    {modified_after}"
    assert max_mtime == repo_mtime, f"last_mtime incorrect for {max_file}"


def test_repo_invisibles(mutable_mock_repo, extra_repo):
    with open(
        os.path.join(extra_repo[0].root, extra_repo[1], ".invisible"), "w", encoding="utf-8"
    ):
        pass
    extra_repo[0].all_package_names()


@pytest.mark.regression("24552")
def test_all_package_names_is_cached_correctly(mock_packages):
    assert "mpi" in spack.repo.all_package_names(include_virtuals=True)
    assert "mpi" not in spack.repo.all_package_names(include_virtuals=False)


@pytest.mark.regression("29203")
def test_use_repositories_doesnt_change_class(mock_packages):
    """Test that we don't create the same package module and class multiple times
    when swapping repositories.
    """
    zlib_cls_outer = spack.repo.PATH.get_pkg_class("zlib")
    current_paths = [r.root for r in spack.repo.PATH.repos]
    with spack.repo.use_repositories(*current_paths):
        zlib_cls_inner = spack.repo.PATH.get_pkg_class("zlib")
    assert id(zlib_cls_inner) == id(zlib_cls_outer)


def test_absolute_import_spack_packages_as_python_modules(mock_packages):
    import spack_repo.builtin_mock.packages.mpileaks.package  # type: ignore[import]

    assert hasattr(spack_repo.builtin_mock.packages.mpileaks.package, "Mpileaks")
    assert isinstance(
        spack_repo.builtin_mock.packages.mpileaks.package.Mpileaks, spack.package_base.PackageMeta
    )
    assert issubclass(
        spack_repo.builtin_mock.packages.mpileaks.package.Mpileaks, spack.package_base.PackageBase
    )


def test_relative_import_spack_packages_as_python_modules(mock_packages):
    from spack_repo.builtin_mock.packages.mpileaks.package import Mpileaks

    assert isinstance(Mpileaks, spack.package_base.PackageMeta)
    assert issubclass(Mpileaks, spack.package_base.PackageBase)


def test_get_all_mock_packages(mock_packages):
    """Get the mock packages once each too."""
    for name in mock_packages.all_package_names():
        mock_packages.get_pkg_class(name)


def test_repo_path_handles_package_removal(tmpdir, mock_packages):
    builder = spack.repo.MockRepositoryBuilder(tmpdir, namespace="removal")
    builder.add_package("pkg-c")
    with spack.repo.use_repositories(builder.root, override=False) as repos:
        r = repos.repo_for_pkg("pkg-c")
        assert r.namespace == "removal"

    builder.remove("pkg-c")
    with spack.repo.use_repositories(builder.root, override=False) as repos:
        r = repos.repo_for_pkg("pkg-c")
        assert r.namespace == "builtin_mock"


def test_repo_dump_virtuals(tmpdir, mutable_mock_repo, mock_packages, ensure_debug, capsys):
    # Start with a package-less virtual
    vspec = spack.spec.Spec("something")
    mutable_mock_repo.dump_provenance(vspec, tmpdir)
    captured = capsys.readouterr()[1]
    assert "does not have a package" in captured

    # Now with a virtual with a package
    vspec = spack.spec.Spec("externalvirtual")
    mutable_mock_repo.dump_provenance(vspec, tmpdir)
    captured = capsys.readouterr()[1]
    assert "Installing" in captured
    assert "package.py" in os.listdir(tmpdir), "Expected the virtual's package to be copied"


@pytest.mark.parametrize("repos", [["mock"], ["extra"], ["mock", "extra"], ["extra", "mock"]])
def test_repository_construction_doesnt_use_globals(nullify_globals, tmp_path, repos):
    def _repo_descriptors(repos):
        descriptors = {}
        for entry in repos:
            if entry == "mock":
                descriptors["builtin_mock"] = spack.repo.LocalRepoDescriptor(
                    "builtin_mock", spack.paths.mock_packages_path
                )
            if entry == "extra":
                name = "extra_mock"
                repo_dir = tmp_path / name
                repo_dir.mkdir()
                repo = spack.repo.MockRepositoryBuilder(repo_dir, name)
                descriptors[name] = spack.repo.LocalRepoDescriptor(name, repo.root)
        return spack.repo.RepoDescriptors(descriptors)

    descriptors = _repo_descriptors(repos)

    repo_cache = spack.util.file_cache.FileCache(tmp_path / "cache")
    repo_path = spack.repo.RepoPath.from_descriptors(descriptors, cache=repo_cache)
    assert len(repo_path.repos) == len(descriptors)
    assert [x.namespace for x in repo_path.repos] == list(descriptors.keys())


@pytest.mark.parametrize("method_name", ["dirname_for_package_name", "filename_for_package_name"])
def test_path_computation_with_names(method_name, mock_packages_repo):
    """Tests that repositories can compute the correct paths when using both fully qualified
    names and unqualified names.
    """
    repo_path = spack.repo.RepoPath(mock_packages_repo)
    method = getattr(repo_path, method_name)
    unqualified = method("mpileaks")
    qualified = method("builtin_mock.mpileaks")
    assert qualified == unqualified


def test_use_repositories_and_import():
    """Tests that use_repositories changes the import search too"""
    import spack.paths

    repo_dir = pathlib.Path(spack.paths.test_repos_path)
    with spack.repo.use_repositories(str(repo_dir / "spack_repo" / "compiler_runtime_test")):
        import spack_repo.compiler_runtime_test.packages.gcc_runtime.package  # type: ignore[import]  # noqa: E501

    with spack.repo.use_repositories(str(repo_dir / "spack_repo" / "builtin_mock")):
        import spack_repo.builtin_mock.packages.cmake.package  # type: ignore[import]  # noqa: F401


@pytest.mark.usefixtures("nullify_globals")
class TestRepo:
    """Test that the Repo class work correctly, and does not depend on globals,
    except the REPOS_FINDER.
    """

    def test_creation(self, mock_test_cache):
        repo = spack.repo.Repo(spack.paths.mock_packages_path, cache=mock_test_cache)
        assert repo.config_file.endswith("repo.yaml")
        assert repo.namespace == "builtin_mock"

    @pytest.mark.parametrize(
        "name,expected", [("mpi", True), ("mpich", False), ("mpileaks", False)]
    )
    def test_is_virtual(self, name, expected, mock_test_cache):
        repo = spack.repo.Repo(spack.paths.mock_packages_path, cache=mock_test_cache)
        assert repo.is_virtual(name) is expected
        assert repo.is_virtual_safe(name) is expected

        repo_path = spack.repo.RepoPath(repo)
        assert repo_path.is_virtual(name) is expected
        assert repo_path.is_virtual_safe(name) is expected

    @pytest.mark.parametrize(
        "module_name,pkg_name",
        [
            ("dla_future", "dla-future"),
            ("num7zip", "7zip"),
            # If no package is there, None is returned
            ("unknown", None),
        ],
    )
    def test_real_name(self, module_name, pkg_name, mock_test_cache, tmp_path):
        """Test that we can correctly compute the 'real' name of a package, from the one
        used to import the Python module.
        """
        path, _ = spack.repo.create_repo(str(tmp_path), package_api=(1, 0))
        if pkg_name is not None:
            pkg_path = pathlib.Path(path) / "packages" / pkg_name / "package.py"
            pkg_path.parent.mkdir(parents=True)
            pkg_path.write_text("")
        repo = spack.repo.Repo(
            path, cache=spack.util.file_cache.FileCache(str(tmp_path / "cache"))
        )
        assert repo.real_name(module_name) == pkg_name

    @pytest.mark.parametrize("name", ["mpileaks", "7zip", "dla-future"])
    def test_get(self, name, mock_test_cache):
        repo = spack.repo.Repo(spack.paths.mock_packages_path, cache=mock_test_cache)
        mock_spec = spack.spec.Spec(name)
        mock_spec._mark_concrete()
        pkg = repo.get(mock_spec)
        assert pkg.__class__ == repo.get_pkg_class(name)

    @pytest.mark.parametrize("virtual_name,expected", [("mpi", ["mpich", "zmpi"])])
    def test_providers(self, virtual_name, expected, mock_test_cache):
        repo = spack.repo.Repo(spack.paths.mock_packages_path, cache=mock_test_cache)
        provider_names = {x.name for x in repo.providers_for(virtual_name)}
        assert provider_names.issuperset(expected)

    @pytest.mark.parametrize(
        "extended,expected",
        [("python", ["py-extension1", "python-venv"]), ("perl", ["perl-extension"])],
    )
    def test_extensions(self, extended, expected, mock_test_cache):
        repo = spack.repo.Repo(spack.paths.mock_packages_path, cache=mock_test_cache)
        repo_path = spack.repo.RepoPath(repo)
        for instance in (repo, repo_path):
            provider_names = {x.name for x in instance.extensions_for(extended)}
            assert provider_names.issuperset(expected)

    def test_all_package_names(self, mock_test_cache):
        repo = spack.repo.Repo(spack.paths.mock_packages_path, cache=mock_test_cache)
        repo_path = spack.repo.RepoPath(repo)

        for instance in (repo, repo_path):
            all_names = instance.all_package_names(include_virtuals=True)
            real_names = instance.all_package_names(include_virtuals=False)
            assert set(all_names).issuperset(real_names)
            for name in set(all_names) - set(real_names):
                assert instance.is_virtual(name)
                assert instance.is_virtual_safe(name)

    def test_packages_with_tags(self, mock_test_cache):
        repo = spack.repo.Repo(spack.paths.mock_packages_path, cache=mock_test_cache)
        repo_path = spack.repo.RepoPath(repo)

        for instance in (repo, repo_path):
            r1 = instance.packages_with_tags("tag1")
            r2 = instance.packages_with_tags("tag1", "tag2")
            assert "mpich" in r1 and "mpich" in r2
            assert "mpich2" in r1 and "mpich2" not in r2
            assert r2.issubset(r1)


@pytest.mark.usefixtures("nullify_globals")
class TestRepoPath:
    def test_creation_from_string(self, mock_test_cache):
        repo = spack.repo.RepoPath.from_descriptors(
            spack.repo.RepoDescriptors(
                {
                    "builtin_mock": spack.repo.LocalRepoDescriptor(
                        "builtin_mock", spack.paths.mock_packages_path
                    )
                }
            ),
            cache=mock_test_cache,
        )
        assert len(repo.repos) == 1
        assert repo.by_namespace["builtin_mock"] is repo.repos[0]

    def test_get_repo(self, mock_test_cache):
        repo = spack.repo.RepoPath.from_descriptors(
            spack.repo.RepoDescriptors(
                {
                    "builtin_mock": spack.repo.LocalRepoDescriptor(
                        "builtin_mock", spack.paths.mock_packages_path
                    )
                }
            ),
            cache=mock_test_cache,
        )
        # builtin_mock is there
        assert repo.get_repo("builtin_mock") is repo.repos[0]
        # foo is not there, raise
        with pytest.raises(spack.repo.UnknownNamespaceError):
            repo.get_repo("foo")


def test_parse_package_api_version():
    """Test that we raise an error if a repository has a version that is not supported."""
    # valid version
    assert spack.repo._parse_package_api_version(
        {"api": "v1.2"}, min_api=(1, 0), max_api=(2, 3)
    ) == (1, 2)
    # too new and too old
    with pytest.raises(
        spack.repo.BadRepoError,
        match=r"Package API v2.4 is not supported .* \(must be between v1.0 and v2.3\)",
    ):
        spack.repo._parse_package_api_version({"api": "v2.4"}, min_api=(1, 0), max_api=(2, 3))
    with pytest.raises(
        spack.repo.BadRepoError,
        match=r"Package API v0.9 is not supported .* \(must be between v1.0 and v2.3\)",
    ):
        spack.repo._parse_package_api_version({"api": "v0.9"}, min_api=(1, 0), max_api=(2, 3))
    # default to v1.0 if not specified
    assert spack.repo._parse_package_api_version({}, min_api=(1, 0), max_api=(2, 3)) == (1, 0)
    # if v1.0 support is dropped we should also raise
    with pytest.raises(
        spack.repo.BadRepoError,
        match=r"Package API v1.0 is not supported .* \(must be between v2.0 and v2.3\)",
    ):
        spack.repo._parse_package_api_version({}, min_api=(2, 0), max_api=(2, 3))
    # finally test invalid input
    with pytest.raises(spack.repo.BadRepoError, match="Invalid Package API version"):
        spack.repo._parse_package_api_version({"api": "v2"}, min_api=(1, 0), max_api=(3, 3))
    with pytest.raises(spack.repo.BadRepoError, match="Invalid Package API version"):
        spack.repo._parse_package_api_version({"api": 2.0}, min_api=(1, 0), max_api=(3, 3))


def test_repo_package_api_version(tmp_path: pathlib.Path):
    """Test that we can specify the API version of a repository."""
    (tmp_path / "example" / "packages").mkdir(parents=True)
    (tmp_path / "example" / "repo.yaml").write_text(
        """\
repo:
    namespace: example
"""
    )
    cache = spack.util.file_cache.FileCache(tmp_path / "cache")
    assert spack.repo.Repo(str(tmp_path / "example"), cache=cache).package_api == (1, 0)


def test_mod_to_pkg_name_and_reverse():
    # In repo v1 the dirname/module name is the package name
    assert spack.util.naming.pkg_dir_to_pkg_name("zlib_ng", package_api=(1, 0)) == "zlib_ng"
    assert (
        spack.util.naming.pkg_dir_to_pkg_name("_3example_4", package_api=(1, 0)) == "_3example_4"
    )
    assert spack.util.naming.pkg_name_to_pkg_dir("zlib_ng", package_api=(1, 0)) == "zlib_ng"
    assert (
        spack.util.naming.pkg_name_to_pkg_dir("_3example_4", package_api=(1, 0)) == "_3example_4"
    )

    # In repo v2 there is a 1-1 mapping between module and package names
    assert spack.util.naming.pkg_dir_to_pkg_name("_3example_4", package_api=(2, 0)) == "3example-4"
    assert spack.util.naming.pkg_dir_to_pkg_name("zlib_ng", package_api=(2, 0)) == "zlib-ng"
    assert spack.util.naming.pkg_name_to_pkg_dir("zlib-ng", package_api=(2, 0)) == "zlib_ng"
    assert spack.util.naming.pkg_name_to_pkg_dir("3example-4", package_api=(2, 0)) == "_3example_4"

    # reserved names need an underscore
    assert spack.util.naming.pkg_dir_to_pkg_name("_finally", package_api=(2, 0)) == "finally"
    assert spack.util.naming.pkg_dir_to_pkg_name("_assert", package_api=(2, 0)) == "assert"
    assert spack.util.naming.pkg_name_to_pkg_dir("finally", package_api=(2, 0)) == "_finally"
    assert spack.util.naming.pkg_name_to_pkg_dir("assert", package_api=(2, 0)) == "_assert"

    # reserved names are case sensitive, so true/false/none are ok
    assert spack.util.naming.pkg_dir_to_pkg_name("true", package_api=(2, 0)) == "true"
    assert spack.util.naming.pkg_dir_to_pkg_name("none", package_api=(2, 0)) == "none"
    assert spack.util.naming.pkg_name_to_pkg_dir("true", package_api=(2, 0)) == "true"
    assert spack.util.naming.pkg_name_to_pkg_dir("none", package_api=(2, 0)) == "none"


def test_repo_v2_invalid_module_name(tmp_path: pathlib.Path, capsys):
    # Create a repo with a v2 structure
    root, _ = spack.repo.create_repo(str(tmp_path), namespace="repo_1", package_api=(2, 0))
    repo_dir = pathlib.Path(root)

    # Create two invalid module names
    (repo_dir / "packages" / "zlib-ng").mkdir()
    (repo_dir / "packages" / "zlib-ng" / "package.py").write_text(
        """
from spack_repo.builtin_mock.build_systems.generic import Package

class ZlibNg(Package):
    pass
"""
    )
    (repo_dir / "packages" / "UPPERCASE").mkdir()
    (repo_dir / "packages" / "UPPERCASE" / "package.py").write_text(
        """
from spack_repo.builtin_mock.build_systems.generic import Package

class Uppercase(Package):
    pass
"""
    )

    with spack.repo.use_repositories(str(repo_dir)) as repo:
        assert len(repo.all_package_names()) == 0

    stderr = capsys.readouterr().err
    assert "cannot be used because `zlib-ng` is not a valid Spack package module name" in stderr
    assert "cannot be used because `UPPERCASE` is not a valid Spack package module name" in stderr


def test_repo_v2_module_and_class_to_package_name(tmp_path: pathlib.Path, capsys):
    # Create a repo with a v2 structure
    root, _ = spack.repo.create_repo(str(tmp_path), namespace="repo_2", package_api=(2, 0))
    repo_dir = pathlib.Path(root)

    # Create an invalid module name
    (repo_dir / "packages" / "_1example_2_test").mkdir()
    (repo_dir / "packages" / "_1example_2_test" / "package.py").write_text(
        """
from spack_repo.builtin_mock.build_systems.generic import Package

class _1example2Test(Package):
    pass
"""
    )

    with spack.repo.use_repositories(str(repo_dir)) as repo:
        assert repo.exists("1example-2-test")
        pkg_cls = repo.get_pkg_class("1example-2-test")
        assert pkg_cls.name == "1example-2-test"
        assert pkg_cls.module.__name__ == "spack_repo.repo_2.packages._1example_2_test.package"


def test_valid_module_name_v2():
    api = (2, 0)

    # no hyphens
    assert not valid_module_name("zlib-ng", api)

    # cannot start with a number
    assert not valid_module_name("7zip", api)

    # no consecutive underscores
    assert not valid_module_name("zlib__ng", api)

    # reserved names
    assert not valid_module_name("finally", api)
    assert not valid_module_name("assert", api)

    # cannot contain uppercase
    assert not valid_module_name("False", api)
    assert not valid_module_name("zlib_NG", api)

    # reserved names are allowed when preceded by underscore
    assert valid_module_name("_finally", api)
    assert valid_module_name("_assert", api)

    # digits are allowed when preceded by underscore
    assert valid_module_name("_1example_2_test", api)

    # underscore is not allowed unless followed by reserved name or digit
    assert not valid_module_name("_zlib", api)
    assert not valid_module_name("_false", api)


def test_namespace_is_optional_in_v2(tmp_path: pathlib.Path):
    """Test that a repo without a namespace is valid in v2."""
    repo_yaml_dir = tmp_path / "spack_repo" / "foo" / "bar" / "baz"
    (repo_yaml_dir / "packages").mkdir(parents=True)
    (repo_yaml_dir / "repo.yaml").write_text(
        """\
repo:
  api: v2.0
"""
    )

    cache = spack.util.file_cache.FileCache(tmp_path / "cache")
    repo = spack.repo.Repo(str(repo_yaml_dir), cache=cache)

    assert repo.namespace == "foo.bar.baz"
    assert repo.full_namespace == "spack_repo.foo.bar.baz.packages"
    assert repo.root == str(repo_yaml_dir)
    assert repo.packages_path == str(repo_yaml_dir / "packages")
    assert repo.python_path == str(tmp_path)
    assert repo.package_api == (2, 0)


def test_subdir_in_v2():
    """subdir cannot be . or empty in v2, because otherwise we cannot statically distinguish
    between namespace and subdir."""
    with pytest.raises(spack.repo.BadRepoError, match="Use a symlink packages -> . instead"):
        spack.repo._validate_and_normalize_subdir(subdir="", root="root", package_api=(2, 0))

    with pytest.raises(spack.repo.BadRepoError, match="Use a symlink packages -> . instead"):
        spack.repo._validate_and_normalize_subdir(subdir=".", root="root", package_api=(2, 0))

    with pytest.raises(spack.repo.BadRepoError, match="Expected a directory name, not a path"):
        subdir = os.path.join("a", "b")
        spack.repo._validate_and_normalize_subdir(subdir=subdir, root="root", package_api=(2, 0))

    with pytest.raises(spack.repo.BadRepoError, match="Must be a valid Python module name"):
        spack.repo._validate_and_normalize_subdir(subdir="123", root="root", package_api=(2, 0))


def test_is_package_module():
    assert spack.repo.is_package_module("spack.pkg.something.something")
    assert spack.repo.is_package_module("spack_repo.foo.bar.baz.package")
    assert not spack.repo.is_package_module("spack_repo.builtin.build_systems.cmake")
    assert not spack.repo.is_package_module("spack.something.else")


def test_environment_activation_updates_repo_path(tmp_path: pathlib.Path):
    """Test that the environment activation updates the repo path correctly."""
    repo_root, _ = spack.repo.create_repo(str(tmp_path / "foo"), namespace="bar")
    (tmp_path / "spack.yaml").write_text(
        """\
spack:
    repos:
        bar: $env/foo/spack_repo/bar
"""
    )
    env = spack.environment.Environment(tmp_path)

    with env:
        assert any(os.path.samefile(repo_root, r.root) for r in spack.repo.PATH.repos)

    assert not any(os.path.samefile(repo_root, r.root) for r in spack.repo.PATH.repos)

    with env:
        assert any(os.path.samefile(repo_root, r.root) for r in spack.repo.PATH.repos)

    assert not any(os.path.samefile(repo_root, r.root) for r in spack.repo.PATH.repos)


def test_repo_update(tmp_path: pathlib.Path):
    existing_root, _ = spack.repo.create_repo(str(tmp_path), namespace="foo")
    nonexisting_root = str(tmp_path / "nonexisting")
    config = {"repos": [existing_root, nonexisting_root]}
    assert spack.schema.repos.update(config)
    assert config["repos"] == {
        "foo": existing_root,
        # non-existing root is removed for simplicity; would be a warning otherwise.
    }


def test_mock_builtin_repo(mock_packages):
    assert spack.repo.builtin_repo() is spack.repo.PATH.get_repo("builtin_mock")


def test_parse_config_descriptor_git_1(tmp_path: pathlib.Path):
    descriptor = spack.repo.parse_config_descriptor(
        name="name",
        descriptor={
            "git": str(tmp_path / "repo.git"),
            "destination": str(tmp_path / "some/destination"),
        },
        lock=spack.util.lock.Lock(str(tmp_path / "x"), enable=False),
    )

    assert isinstance(descriptor, spack.repo.RemoteRepoDescriptor)
    assert descriptor.name == "name"
    assert descriptor.repository == str(tmp_path / "repo.git")
    assert descriptor.destination == str(tmp_path / "some/destination")
    assert descriptor.relative_paths is None


def test_parse_config_descriptor_git_2(tmp_path: pathlib.Path):
    descriptor = spack.repo.parse_config_descriptor(
        name="name",
        descriptor={"git": str(tmp_path / "repo.git"), "paths": ["some/path"]},
        lock=spack.util.lock.Lock(str(tmp_path / "x"), enable=False),
    )
    assert isinstance(descriptor, spack.repo.RemoteRepoDescriptor)
    assert descriptor.relative_paths == ["some/path"]


def test_parse_config_descriptor_local(tmp_path: pathlib.Path):
    descriptor = spack.repo.parse_config_descriptor(
        name="name",
        descriptor=str(tmp_path / "local_repo"),
        lock=spack.util.lock.Lock(str(tmp_path / "x"), enable=False),
    )
    assert isinstance(descriptor, spack.repo.LocalRepoDescriptor)
    assert descriptor.name == "name"
    assert descriptor.path == str(tmp_path / "local_repo")


def test_repo_descriptors_construct(tmp_path: pathlib.Path):
    """Test the RepoDescriptors construct function. Ensure it does not raise when we cannot
    construct a Repo instance, e.g. due to missing repo.yaml file. Check that it parses the
    spack-repo-index.yaml file both when newly initialized and when already cloned."""

    lock = spack.util.lock.Lock(str(tmp_path / "x"), enable=False)
    cache = spack.util.file_cache.FileCache(str(tmp_path / "cache"))

    # Construct 3 identical descriptors
    descriptors_1, descriptors_2, descriptors_3 = [
        {
            "foo": spack.repo.RemoteRepoDescriptor(
                name="foo",
                repository=str(tmp_path / "foo.git"),
                destination=str(tmp_path / "foo_destination"),
                relative_paths=None,
                lock=lock,
            )
        }
        for _ in range(3)
    ]

    repos_1 = spack.repo.RepoDescriptors(descriptors_1)  # type: ignore
    repos_2 = spack.repo.RepoDescriptors(descriptors_2)  # type: ignore
    repos_3 = spack.repo.RepoDescriptors(descriptors_3)  # type: ignore

    git_clone_calls = 0

    class MockGit(spack.util.executable.Executable):
        def __init__(self):
            pass

        def __call__(self, *args, **kwargs) -> str:  # type: ignore
            nonlocal git_clone_calls
            git_clone_calls += 1

            action, flag, repo, dest = args

            assert action == "clone"
            assert flag == "--depth=100"
            assert "foo.git" in repo
            assert "foo_destination" in dest

            # The git repo needs a .git subdir
            os.makedirs(os.path.join(dest, ".git"))

            # The spack-repo-index.yaml is optional; we test Spack reads from it.
            with open(os.path.join(dest, "spack-repo-index.yaml"), "w", encoding="utf-8") as f:
                f.write(
                    """\
repo_index:
  paths:
  - spack_repo/foo
"""
                )

            return ""

    repo_path_1, errors_1 = repos_1.construct(cache=cache, find_git=MockGit)

    # Verify it cannot construct a Repo instance, and that this does *not* throw, since that would
    # break Spack very early on. Instead, an error is returned. Also verify that
    # relative_paths is read from spack-repo-index.yaml.
    assert git_clone_calls == 1
    assert len(repo_path_1.repos) == 0
    assert len(errors_1) == 1
    assert all("No repo.yaml" in str(err) for err in errors_1.values()), errors_1
    assert descriptors_1["foo"].relative_paths == ["spack_repo/foo"]

    # Do the same test with another instance: it should *not* clone a second time.
    repo_path_2, errors_2 = repos_2.construct(cache=cache, find_git=MockGit)
    assert git_clone_calls == 1
    assert len(repo_path_2.repos) == 0
    assert len(errors_2) == 1
    assert all("No repo.yaml" in str(err) for err in errors_2.values()), errors_2
    assert descriptors_1["foo"].relative_paths == ["spack_repo/foo"]

    # Finally fill the repo with an actual repo and check that the repo can be constructed.
    spack.repo.create_repo(str(tmp_path / "foo_destination"), "foo")
    repo_path_3, errors_3 = repos_3.construct(cache=cache, find_git=MockGit)
    assert git_clone_calls == 1
    assert not errors_3
    assert len(repo_path_3.repos) == 1
    assert repo_path_3.repos[0].namespace == "foo"
