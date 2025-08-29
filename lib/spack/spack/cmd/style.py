# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)
import argparse
import ast
import os
import re
import sys
import warnings
from itertools import islice, zip_longest
from typing import Callable, Dict, List, Optional

import spack.llnl.util.tty as tty
import spack.llnl.util.tty.color as color
import spack.paths
import spack.repo
import spack.util.git
import spack.util.spack_yaml
from spack.llnl.util.filesystem import working_dir
from spack.spec_parser import NAME, VERSION_LIST, SpecTokens
from spack.tokenize import Token, TokenBase, Tokenizer
from spack.util.executable import Executable, which

description = "runs source code style checks on spack"
section = "developer"
level = "long"


def grouper(iterable, n, fillvalue=None):
    """Collect data into fixed-length chunks or blocks"""
    # grouper('ABCDEFG', 3, 'x') --> ABC DEF Gxx"
    args = [iter(iterable)] * n
    for group in zip_longest(*args, fillvalue=fillvalue):
        yield filter(None, group)


#: List of paths to exclude from checks -- relative to spack root
exclude_paths = [os.path.relpath(spack.paths.vendor_path, spack.paths.prefix)]

#: Order in which tools should be run. flake8 is last so that it can
#: double-check the results of other tools (if, e.g., ``--fix`` was provided)
#: The list maps an executable name to a method to ensure the tool is
#: bootstrapped or present in the environment.
tool_names = ["import", "isort", "black", "flake8", "mypy"]

#: warnings to ignore in mypy
mypy_ignores = [
    # same as `disable_error_code = "annotation-unchecked"` in pyproject.toml, which
    # doesn't exist in mypy 0.971 for Python 3.6
    "[annotation-unchecked]"
]


def is_package(f):
    """Whether flake8 should consider a file as a core file or a package.

    We run flake8 with different exceptions for the core and for
    packages, since we allow ``from spack.package import *`` and poking globals
    into packages.
    """
    return f.startswith("var/spack/") and f.endswith("package.py")


#: decorator for adding tools to the list
class tool:
    def __init__(self, name: str, required: bool = False, external: bool = True) -> None:
        self.name = name
        self.external = external
        self.required = required

    def __call__(self, fun):
        self.fun = fun
        tools[self.name] = self
        return fun

    @property
    def installed(self) -> bool:
        return bool(which(self.name)) if self.external else True

    @property
    def executable(self) -> Optional[Executable]:
        return which(self.name) if self.external else None


#: tools we run in spack style
tools: Dict[str, tool] = {}


def changed_files(base="develop", untracked=True, all_files=False, root=None):
    """Get list of changed files in the Spack repository.

    Arguments:
        base (str): name of base branch to evaluate differences with.
        untracked (bool): include untracked files in the list.
        all_files (bool): list all files in the repository.
        root (str): use this directory instead of the Spack prefix.
    """
    if root is None:
        root = spack.paths.prefix

    git = spack.util.git.git(required=True)

    # ensure base is in the repo
    base_sha = git(
        "rev-parse", "--quiet", "--verify", "--revs-only", base, fail_on_error=False, output=str
    )
    if git.returncode != 0:
        tty.die(
            "This repository does not have a '%s' revision." % base,
            "spack style needs this branch to determine which files changed.",
            "Ensure that '%s' exists, or specify files to check explicitly." % base,
        )

    range = "{0}...".format(base_sha.strip())

    git_args = [
        # Add changed files committed since branching off of develop
        ["diff", "--name-only", "--diff-filter=ACMR", range],
        # Add changed files that have been staged but not yet committed
        ["diff", "--name-only", "--diff-filter=ACMR", "--cached"],
        # Add changed files that are unstaged
        ["diff", "--name-only", "--diff-filter=ACMR"],
    ]

    # Add new files that are untracked
    if untracked:
        git_args.append(["ls-files", "--exclude-standard", "--other"])

    # add everything if the user asked for it
    if all_files:
        git_args.append(["ls-files", "--exclude-standard"])

    excludes = [os.path.realpath(os.path.join(root, f)) for f in exclude_paths]
    changed = set()

    for arg_list in git_args:
        files = git(*arg_list, output=str).split("\n")

        for f in files:
            # Ignore non-Python files
            if not (f.endswith(".py") or f == "bin/spack"):
                continue

            # Ignore files in the exclude locations
            if any(os.path.realpath(f).startswith(e) for e in excludes):
                continue

            changed.add(f)

    return sorted(changed)


def setup_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "-b",
        "--base",
        action="store",
        default="develop",
        help="branch to compare against to determine changed files (default: develop)",
    )
    subparser.add_argument(
        "-a", "--all", action="store_true", help="check all files, not just changed files"
    )
    subparser.add_argument(
        "-r",
        "--root-relative",
        action="store_true",
        default=False,
        help="print root-relative paths (default: cwd-relative)",
    )
    subparser.add_argument(
        "-U",
        "--no-untracked",
        dest="untracked",
        action="store_false",
        default=True,
        help="exclude untracked files from checks",
    )
    subparser.add_argument(
        "-f",
        "--fix",
        action="store_true",
        default=False,
        help="format automatically if possible (e.g., with isort, black)",
    )
    subparser.add_argument(
        "--root", action="store", default=None, help="style check a different spack instance"
    )

    tool_group = subparser.add_mutually_exclusive_group()
    tool_group.add_argument(
        "-t",
        "--tool",
        action="append",
        help="specify which tools to run (default: %s)" % ", ".join(tool_names),
    )
    tool_group.add_argument(
        "-s",
        "--skip",
        metavar="TOOL",
        action="append",
        help="specify tools to skip (choose from %s)" % ", ".join(tool_names),
    )
    subparser.add_argument(
        "--spec-strings",
        action="store_true",
        help="upgrade spec strings in Python, JSON and YAML files for compatibility with Spack "
        "v1.0 and v0.x. Example: spack style ``--spec-strings $(git ls-files)``. Note: must be "
        "used only on specs from spack v0.X.",
    )

    subparser.add_argument("files", nargs=argparse.REMAINDER, help="specific files to check")


def cwd_relative(path, root, initial_working_dir):
    """Translate prefix-relative path to current working directory-relative."""
    return os.path.relpath(os.path.join(root, path), initial_working_dir)


def rewrite_and_print_output(
    output, args, re_obj=re.compile(r"^(.+):([0-9]+):"), replacement=r"{0}:{1}:"
):
    """rewrite ouput with <file>:<line>: format to respect path args"""

    # print results relative to current working directory
    def translate(match):
        return replacement.format(
            cwd_relative(match.group(1), args.root, args.initial_working_dir),
            *list(match.groups()[1:]),
        )

    for line in output.split("\n"):
        if not line:
            continue
        if any(ignore in line for ignore in mypy_ignores):
            # some mypy annotations can't be disabled in older mypys (e.g. .971, which
            # is the only mypy that supports python 3.6), so we filter them here.
            continue
        if not args.root_relative and re_obj:
            line = re_obj.sub(translate, line)
        print(line)


def print_style_header(file_list, args, tools_to_run):
    tty.msg("Running style checks on spack", "selected: " + ", ".join(tools_to_run))
    # translate modified paths to cwd_relative if needed
    paths = [filename.strip() for filename in file_list]
    if not args.root_relative:
        paths = [cwd_relative(filename, args.root, args.initial_working_dir) for filename in paths]

    tty.msg("Modified files", *paths)
    sys.stdout.flush()


def print_tool_header(tool):
    sys.stdout.flush()
    tty.msg("Running %s checks" % tool)
    sys.stdout.flush()


def print_tool_result(tool, returncode):
    if returncode == 0:
        color.cprint("  @g{%s checks were clean}" % tool)
    else:
        color.cprint("  @r{%s found errors}" % tool)


@tool("flake8", required=True)
def run_flake8(flake8_cmd, file_list, args):
    returncode = 0
    output = ""
    # run in chunks of 100 at a time to avoid line length limit
    # filename parameter in config *does not work* for this reliably
    for chunk in grouper(file_list, 100):
        output = flake8_cmd(
            # always run with config from running spack prefix
            "--config=%s" % os.path.join(spack.paths.prefix, ".flake8"),
            *chunk,
            fail_on_error=False,
            output=str,
        )
        returncode |= flake8_cmd.returncode

        rewrite_and_print_output(output, args)

    print_tool_result("flake8", returncode)
    return returncode


@tool("mypy")
def run_mypy(mypy_cmd, file_list, args):
    # always run with config from running spack prefix
    common_mypy_args = [
        "--config-file",
        os.path.join(spack.paths.prefix, "pyproject.toml"),
        "--show-error-codes",
    ]
    mypy_arg_sets = [common_mypy_args + ["--package", "spack", "--package", "llnl"]]
    if "SPACK_MYPY_CHECK_PACKAGES" in os.environ:
        mypy_arg_sets.append(
            common_mypy_args + ["--package", "packages", "--disable-error-code", "no-redef"]
        )

    returncode = 0
    for mypy_args in mypy_arg_sets:
        output = mypy_cmd(*mypy_args, fail_on_error=False, output=str)
        returncode |= mypy_cmd.returncode

        rewrite_and_print_output(output, args)

    print_tool_result("mypy", returncode)
    return returncode


@tool("isort")
def run_isort(isort_cmd, file_list, args):
    # always run with config from running spack prefix
    isort_args = ("--settings-path", os.path.join(spack.paths.prefix, "pyproject.toml"))
    if not args.fix:
        isort_args += ("--check", "--diff")

    pat = re.compile("ERROR: (.*) Imports are incorrectly sorted")
    replacement = "ERROR: {0} Imports are incorrectly sorted"
    returncode = [0]

    def process_files(file_list, is_args):
        for chunk in grouper(file_list, 100):
            packed_args = is_args + tuple(chunk)
            output = isort_cmd(*packed_args, fail_on_error=False, output=str, error=str)
            returncode[0] |= isort_cmd.returncode

            rewrite_and_print_output(output, args, pat, replacement)

    # packages
    process_files(filter(is_package, file_list), isort_args)
    # non-packages
    process_files(filter(lambda f: not is_package(f), file_list), isort_args)

    print_tool_result("isort", returncode[0])
    return returncode[0]


@tool("black")
def run_black(black_cmd, file_list, args):
    # always run with config from running spack prefix
    black_args = ("--config", os.path.join(spack.paths.prefix, "pyproject.toml"))
    if not args.fix:
        black_args += ("--check", "--diff")
        if color.get_color_when():  # only show color when spack would
            black_args += ("--color",)

    pat = re.compile("would reformat +(.*)")
    replacement = "would reformat {0}"
    returncode = 0
    output = ""
    # run in chunks of 100 at a time to avoid line length limit
    # filename parameter in config *does not work* for this reliably
    for chunk in grouper(file_list, 100):
        packed_args = black_args + tuple(chunk)
        output = black_cmd(*packed_args, fail_on_error=False, output=str, error=str)
        returncode |= black_cmd.returncode
        rewrite_and_print_output(output, args, pat, replacement)

    print_tool_result("black", returncode)

    return returncode


def _module_part(root: str, expr: str):
    parts = expr.split(".")
    # spack.pkg is for repositories, don't try to resolve it here.
    if expr.startswith(spack.repo.PKG_MODULE_PREFIX_V1) or expr == "spack.pkg":
        return None
    while parts:
        f1 = os.path.join(root, "lib", "spack", *parts) + ".py"
        f2 = os.path.join(root, "lib", "spack", *parts, "__init__.py")

        if (
            os.path.exists(f1)
            # ensure case sensitive match
            and f"{parts[-1]}.py" in os.listdir(os.path.dirname(f1))
            or os.path.exists(f2)
        ):
            return ".".join(parts)
        parts.pop()
    return None


def _run_import_check(
    file_list: List[str],
    *,
    fix: bool,
    root_relative: bool,
    root=spack.paths.prefix,
    working_dir=spack.paths.prefix,
    out=sys.stdout,
):
    if sys.version_info < (3, 9):
        print("import check requires Python 3.9 or later")
        return 0

    is_use = re.compile(r"(?<!from )(?<!import )spack\.[a-zA-Z0-9_\.]+")

    # redundant imports followed by a `# comment` are ignored, cause there can be legimitate reason
    # to import a module: execute module scope init code, or to deal with circular imports.
    is_abs_import = re.compile(r"^import (spack\.[a-zA-Z0-9_\.]+)$", re.MULTILINE)

    exit_code = 0

    for file in file_list:
        to_add = set()
        to_remove = []

        pretty_path = file if root_relative else cwd_relative(file, root, working_dir)

        try:
            with open(file, "r", encoding="utf-8") as f:
                contents = f.read()
            parsed = ast.parse(contents)
        except Exception:
            exit_code = 1
            print(f"{pretty_path}: could not parse", file=out)
            continue

        for m in is_abs_import.finditer(contents):
            # Find at most two occurences: the first is the import itself, the second is its usage.
            if len(list(islice(re.finditer(rf"{re.escape(m.group(1))}(?!\w)", contents), 2))) == 1:
                to_remove.append(m.group(0))
                exit_code = 1
                print(f"{pretty_path}: redundant import: {m.group(1)}", file=out)

        # Clear all strings to avoid matching comments/strings etc.
        for node in ast.walk(parsed):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                node.value = ""

        filtered_contents = ast.unparse(parsed)  # novermin
        for m in is_use.finditer(filtered_contents):
            module = _module_part(root, m.group(0))
            if not module or module in to_add:
                continue
            if re.search(rf"import {re.escape(module)}(?!\w|\.)", contents):
                continue
            to_add.add(module)
            exit_code = 1
            print(f"{pretty_path}: missing import: {module} ({m.group(0)})", file=out)

        if not fix or not to_add and not to_remove:
            continue

        with open(file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        if to_add:
            # insert missing imports before the first import, delegate ordering to isort
            for node in parsed.body:
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    first_line = node.lineno
                    break
            else:
                print(f"{pretty_path}: could not fix", file=out)
                continue
            lines.insert(first_line, "\n".join(f"import {x}" for x in to_add) + "\n")

        new_contents = "".join(lines)

        # remove redundant imports
        for statement in to_remove:
            new_contents = new_contents.replace(f"{statement}\n", "")

        with open(file, "w", encoding="utf-8") as f:
            f.write(new_contents)

    return exit_code


@tool("import", external=False)
def run_import_check(import_check_cmd, file_list, args):
    exit_code = _run_import_check(
        file_list,
        fix=args.fix,
        root_relative=args.root_relative,
        root=args.root,
        working_dir=args.initial_working_dir,
    )
    print_tool_result("import", exit_code)
    return exit_code


def validate_toolset(arg_value):
    """Validate ``--tool`` and ``--skip`` arguments (sets of optionally comma-separated tools)."""
    tools = set(",".join(arg_value).split(","))  # allow args like 'isort,flake8'
    for tool in tools:
        if tool not in tool_names:
            tty.die("Invalid tool: '%s'" % tool, "Choose from: %s" % ", ".join(tool_names))
    return tools


def missing_tools(tools_to_run: List[str]) -> List[str]:
    return [t for t in tools_to_run if not tools[t].installed]


def _bootstrap_dev_dependencies():
    import spack.bootstrap

    with spack.bootstrap.ensure_bootstrap_configuration():
        spack.bootstrap.ensure_environment_dependencies()


IS_PROBABLY_COMPILER = re.compile(r"%[a-zA-Z_][a-zA-Z0-9\-]")


class _LegacySpecTokens(TokenBase):
    """Reconstructs the tokens for previous specs, so we can reuse code to rotate them"""

    # Dependency
    START_EDGE_PROPERTIES = r"(?:\^\[)"
    END_EDGE_PROPERTIES = r"(?:\])"
    DEPENDENCY = r"(?:\^)"
    # Version
    VERSION_HASH_PAIR = SpecTokens.VERSION_HASH_PAIR.regex
    GIT_VERSION = SpecTokens.GIT_VERSION.regex
    VERSION = SpecTokens.VERSION.regex
    # Variants
    PROPAGATED_BOOL_VARIANT = SpecTokens.PROPAGATED_BOOL_VARIANT.regex
    BOOL_VARIANT = SpecTokens.BOOL_VARIANT.regex
    PROPAGATED_KEY_VALUE_PAIR = SpecTokens.PROPAGATED_KEY_VALUE_PAIR.regex
    KEY_VALUE_PAIR = SpecTokens.KEY_VALUE_PAIR.regex
    # Compilers
    COMPILER_AND_VERSION = rf"(?:%\s*(?:{NAME})(?:[\s]*)@\s*(?:{VERSION_LIST}))"
    COMPILER = rf"(?:%\s*(?:{NAME}))"
    # FILENAME
    FILENAME = SpecTokens.FILENAME.regex
    # Package name
    FULLY_QUALIFIED_PACKAGE_NAME = SpecTokens.FULLY_QUALIFIED_PACKAGE_NAME.regex
    UNQUALIFIED_PACKAGE_NAME = SpecTokens.UNQUALIFIED_PACKAGE_NAME.regex
    # DAG hash
    DAG_HASH = SpecTokens.DAG_HASH.regex
    # White spaces
    WS = SpecTokens.WS.regex
    # Unexpected character(s)
    UNEXPECTED = SpecTokens.UNEXPECTED.regex


def _spec_str_reorder_compiler(idx: int, blocks: List[List[Token]]) -> None:
    # only move the compiler to the back if it exists and is not already at the end
    if not 0 <= idx < len(blocks) - 1:
        return
    # if there's only whitespace after the compiler, don't move it
    if all(token.kind == _LegacySpecTokens.WS for block in blocks[idx + 1 :] for token in block):
        return
    # rotate left and always add at least one WS token between compiler and previous token
    compiler_block = blocks.pop(idx)
    if compiler_block[0].kind != _LegacySpecTokens.WS:
        compiler_block.insert(0, Token(_LegacySpecTokens.WS, " "))
    # delete the WS tokens from the new first block if it was at the very start, to prevent leading
    # WS tokens.
    while idx == 0 and blocks[0][0].kind == _LegacySpecTokens.WS:
        blocks[0].pop(0)
    blocks.append(compiler_block)


def _spec_str_format(spec_str: str) -> Optional[str]:
    """Given any string, try to parse as spec string, and rotate the compiler token to the end
    of each spec instance. Returns the formatted string if it was changed, otherwise None."""
    # We parse blocks of tokens that include leading whitespace, and move the compiler block to
    # the end when we hit a dependency ^... or the end of a string.
    # [@3.1][ +foo][ +bar][ %gcc@3.1][ +baz]
    # [@3.1][ +foo][ +bar][ +baz][ %gcc@3.1]

    current_block: List[Token] = []
    blocks: List[List[Token]] = []
    compiler_block_idx = -1
    in_edge_attr = False

    legacy_tokenizer = Tokenizer(_LegacySpecTokens)

    for token in legacy_tokenizer.tokenize(spec_str):
        if token.kind == _LegacySpecTokens.UNEXPECTED:
            # parsing error, we cannot fix this string.
            return None
        elif token.kind in (_LegacySpecTokens.COMPILER, _LegacySpecTokens.COMPILER_AND_VERSION):
            # multiple compilers are not supported in Spack v0.x, so early return
            if compiler_block_idx != -1:
                return None
            current_block.append(token)
            blocks.append(current_block)
            current_block = []
            compiler_block_idx = len(blocks) - 1
        elif token.kind in (
            _LegacySpecTokens.START_EDGE_PROPERTIES,
            _LegacySpecTokens.DEPENDENCY,
            _LegacySpecTokens.UNQUALIFIED_PACKAGE_NAME,
            _LegacySpecTokens.FULLY_QUALIFIED_PACKAGE_NAME,
        ):
            _spec_str_reorder_compiler(compiler_block_idx, blocks)
            compiler_block_idx = -1
            if token.kind == _LegacySpecTokens.START_EDGE_PROPERTIES:
                in_edge_attr = True
            current_block.append(token)
            blocks.append(current_block)
            current_block = []
        elif token.kind == _LegacySpecTokens.END_EDGE_PROPERTIES:
            in_edge_attr = False
            current_block.append(token)
            blocks.append(current_block)
            current_block = []
        elif in_edge_attr:
            current_block.append(token)
        elif token.kind in (
            _LegacySpecTokens.VERSION_HASH_PAIR,
            _LegacySpecTokens.GIT_VERSION,
            _LegacySpecTokens.VERSION,
            _LegacySpecTokens.PROPAGATED_BOOL_VARIANT,
            _LegacySpecTokens.BOOL_VARIANT,
            _LegacySpecTokens.PROPAGATED_KEY_VALUE_PAIR,
            _LegacySpecTokens.KEY_VALUE_PAIR,
            _LegacySpecTokens.DAG_HASH,
        ):
            current_block.append(token)
            blocks.append(current_block)
            current_block = []
        elif token.kind == _LegacySpecTokens.WS:
            current_block.append(token)
        else:
            raise ValueError(f"unexpected token {token}")

    if current_block:
        blocks.append(current_block)
    _spec_str_reorder_compiler(compiler_block_idx, blocks)

    new_spec_str = "".join(token.value for block in blocks for token in block)
    return new_spec_str if spec_str != new_spec_str else None


SpecStrHandler = Callable[[str, int, int, str, str], None]


def _spec_str_default_handler(path: str, line: int, col: int, old: str, new: str):
    """A SpecStrHandler that prints formatted spec strings and their locations."""
    print(f"{path}:{line}:{col}: `{old}` -> `{new}`")


def _spec_str_fix_handler(path: str, line: int, col: int, old: str, new: str):
    """A SpecStrHandler that updates formatted spec strings in files."""
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    new_line = lines[line - 1].replace(old, new)
    if new_line == lines[line - 1]:
        tty.warn(f"{path}:{line}:{col}: could not apply fix: `{old}` -> `{new}`")
        return
    lines[line - 1] = new_line
    print(f"{path}:{line}:{col}: fixed `{old}` -> `{new}`")
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def _spec_str_ast(path: str, tree: ast.AST, handler: SpecStrHandler) -> None:
    """Walk the AST of a Python file and apply handler to formatted spec strings."""
    has_constant = sys.version_info >= (3, 8)
    for node in ast.walk(tree):
        if has_constant and isinstance(node, ast.Constant) and isinstance(node.value, str):
            current_str = node.value
        elif not has_constant and isinstance(node, ast.Str):
            current_str = node.s
        else:
            continue
        if not IS_PROBABLY_COMPILER.search(current_str):
            continue
        new = _spec_str_format(current_str)
        if new is not None:
            handler(path, node.lineno, node.col_offset, current_str, new)


def _spec_str_json_and_yaml(path: str, data: dict, handler: SpecStrHandler) -> None:
    """Walk a YAML or JSON data structure and apply handler to formatted spec strings."""
    queue = [data]
    seen = set()

    while queue:
        current = queue.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, dict):
            queue.extend(current.values())
            queue.extend(current.keys())
        elif isinstance(current, list):
            queue.extend(current)
        elif isinstance(current, str) and IS_PROBABLY_COMPILER.search(current):
            new = _spec_str_format(current)
            if new is not None:
                mark = getattr(current, "_start_mark", None)
                if mark:
                    line, col = mark.line + 1, mark.column + 1
                else:
                    line, col = 0, 0
                handler(path, line, col, current, new)


def _check_spec_strings(
    paths: List[str], handler: SpecStrHandler = _spec_str_default_handler
) -> None:
    """Open Python, JSON and YAML files, and format their string literals that look like spec
    strings. A handler is called for each formatting, which can be used to print or apply fixes."""
    for path in paths:
        is_json_or_yaml = path.endswith(".json") or path.endswith(".yaml") or path.endswith(".yml")
        is_python = path.endswith(".py")
        if not is_json_or_yaml and not is_python:
            continue

        try:
            with open(path, "r", encoding="utf-8") as f:
                # skip files that are likely too large to be user code or config
                if os.fstat(f.fileno()).st_size > 1024 * 1024:
                    warnings.warn(f"skipping {path}: too large.")
                    continue
                if is_json_or_yaml:
                    _spec_str_json_and_yaml(path, spack.util.spack_yaml.load_config(f), handler)
                elif is_python:
                    _spec_str_ast(path, ast.parse(f.read()), handler)
        except (OSError, spack.util.spack_yaml.SpackYAMLError, SyntaxError, ValueError):
            warnings.warn(f"skipping {path}")
            continue


def style(parser, args):
    if args.spec_strings:
        if not args.files:
            tty.die("No files provided to check spec strings.")
        handler = _spec_str_fix_handler if args.fix else _spec_str_default_handler
        return _check_spec_strings(args.files, handler)

    # save initial working directory for relativizing paths later
    args.initial_working_dir = os.getcwd()

    # ensure that the config files we need actually exist in the spack prefix.
    # assertions b/c users should not ever see these errors -- they're checked in CI.
    assert os.path.isfile(os.path.join(spack.paths.prefix, "pyproject.toml"))
    assert os.path.isfile(os.path.join(spack.paths.prefix, ".flake8"))

    # validate spack root if the user provided one
    args.root = os.path.realpath(args.root) if args.root else spack.paths.prefix
    spack_script = os.path.join(args.root, "bin", "spack")
    if not os.path.exists(spack_script):
        tty.die("This does not look like a valid spack root.", "No such file: '%s'" % spack_script)

    file_list = args.files
    if file_list:

        def prefix_relative(path):
            return os.path.relpath(os.path.abspath(os.path.realpath(path)), args.root)

        file_list = [prefix_relative(p) for p in file_list]

    # process --tool and --skip arguments
    selected = set(tool_names)
    if args.tool is not None:
        selected = validate_toolset(args.tool)
    if args.skip is not None:
        selected -= validate_toolset(args.skip)

    if not selected:
        tty.msg("Nothing to run.")
        return

    tools_to_run = [t for t in tool_names if t in selected]
    if missing_tools(tools_to_run):
        _bootstrap_dev_dependencies()

    return_code = 0
    with working_dir(args.root):
        if not file_list:
            file_list = changed_files(args.base, args.untracked, args.all)

        print_style_header(file_list, args, tools_to_run)
        for tool_name in tools_to_run:
            tool = tools[tool_name]
            print_tool_header(tool_name)
            return_code |= tool.fun(tool.executable, file_list, args)

    if return_code == 0:
        tty.msg(color.colorize("@*{spack style checks were clean}"))
    else:
        tty.error(color.colorize("@*{spack style found errors}"))

    return return_code
