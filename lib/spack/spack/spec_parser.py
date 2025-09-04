# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)
"""Parser for spec literals

Here is the EBNF grammar for a spec::

    spec          = [name] [node_options] { ^[edge_properties] node } |
                    [name] [node_options] hash |
                    filename

    node          =  name [node_options] |
                     [name] [node_options] hash |
                     filename

    node_options    = [@(version_list|version_pair)] [%compiler] { variant }
    edge_properties = [ { bool_variant | key_value } ]

    hash          = / id
    filename      = (.|/|[a-zA-Z0-9-_]*/)([a-zA-Z0-9-_./]*)(.json|.yaml)

    name          = id | namespace id
    namespace     = { id . }

    variant       = bool_variant | key_value | propagated_bv | propagated_kv
    bool_variant  =  +id |  ~id |  -id
    propagated_bv = ++id | ~~id | --id
    key_value     =  id=id |  id=quoted_id
    propagated_kv = id==id | id==quoted_id

    compiler      = id [@version_list]

    version_pair  = git_version=vid
    version_list  = (version|version_range) [ { , (version|version_range)} ]
    version_range = vid:vid | vid: | :vid | :
    version       = vid

    git_version   = git.(vid) | git_hash
    git_hash      = [A-Fa-f0-9]{40}

    quoted_id     = " id_with_ws " | ' id_with_ws '
    id_with_ws    = [a-zA-Z0-9_][a-zA-Z_0-9-.\\s]*
    vid           = [a-zA-Z0-9_][a-zA-Z_0-9-.]*
    id            = [a-zA-Z0-9_][a-zA-Z_0-9-]*

Identifiers using the ``<name>=<value>`` command, such as architectures and
compiler flags, require a space before the name.

There is one context-sensitive part: ids in versions may contain ``.``, while
other ids may not.

There is one ambiguity: since ``-`` is allowed in an id, you need to put
whitespace space before ``-variant`` for it to be tokenized properly.  You can
either use whitespace, or you can just use ``~variant`` since it means the same
thing.  Spack uses ``~variant`` in directory names and in the canonical form of
specs to avoid ambiguity.  Both are provided because ``~`` can cause shell
expansion when it is the first character in an id typed on the command line.
"""
import json
import pathlib
import re
import sys
import traceback
import warnings
from typing import TYPE_CHECKING, Dict, Iterator, List, Optional, Tuple, Union

import spack.config
import spack.deptypes
import spack.error
import spack.paths
import spack.util.spack_yaml
import spack.version
from spack.aliases import LEGACY_COMPILER_TO_BUILTIN
from spack.llnl.util.tty import color
from spack.tokenize import Token, TokenBase, Tokenizer

if TYPE_CHECKING:
    import spack.spec

#: Valid name for specs and variants. Here we are not using
#: the previous ``w[\w.-]*`` since that would match most
#: characters that can be part of a word in any language
IDENTIFIER = r"(?:[a-zA-Z_0-9][a-zA-Z_0-9\-]*)"
DOTTED_IDENTIFIER = rf"(?:{IDENTIFIER}(?:\.{IDENTIFIER})+)"
GIT_HASH = r"(?:[A-Fa-f0-9]{40})"
#: Git refs include branch names, and can contain ``.`` and ``/``
GIT_REF = r"(?:[a-zA-Z_0-9][a-zA-Z_0-9./\-]*)"
GIT_VERSION_PATTERN = rf"(?:(?:git\.(?:{GIT_REF}))|(?:{GIT_HASH}))"

#: Substitute a package for a virtual, e.g., c,cxx=gcc.
#: NOTE: Overlaps w/KVP; this should be first if matched in sequence.
VIRTUAL_ASSIGNMENT = (
    r"(?:"
    rf"(?P<virtuals>{IDENTIFIER}(?:,{IDENTIFIER})*)"  # comma-separated virtuals
    rf"=(?P<substitute>{DOTTED_IDENTIFIER}|{IDENTIFIER})"  # package to substitute
    r")"
)

STAR = r"\*"

NAME = r"[a-zA-Z_0-9][a-zA-Z_0-9\-.]*"

HASH = r"[a-zA-Z_0-9]+"

#: These are legal values that *can* be parsed bare, without quotes on the command line.
VALUE = r"(?:[a-zA-Z_0-9\-+\*.,:=%^\~\/\\]+)"

#: Quoted values can be *anything* in between quotes, including escaped quotes.
QUOTED_VALUE = r"(?:'(?:[^']|(?<=\\)')*'|\"(?:[^\"]|(?<=\\)\")*\")"

VERSION = r"=?(?:[a-zA-Z0-9_][a-zA-Z_0-9\-\.]*\b)"
VERSION_RANGE = rf"(?:(?:{VERSION})?:(?:{VERSION}(?!\s*=))?)"
VERSION_LIST = rf"(?:{VERSION_RANGE}|{VERSION})(?:\s*,\s*(?:{VERSION_RANGE}|{VERSION}))*"

SPLIT_KVP = re.compile(rf"^({NAME})(:?==?)(.*)$")

#: A filename starts either with a ``.`` or a ``/`` or a ``{name}/``, or on Windows, a drive letter
#: followed by a colon and ``\`` or ``.`` or ``{name}\``
WINDOWS_FILENAME = r"(?:\.|[a-zA-Z0-9-_]*\\|[a-zA-Z]:\\)(?:[a-zA-Z0-9-_\.\\]*)(?:\.json|\.yaml)"
UNIX_FILENAME = r"(?:\.|\/|[a-zA-Z0-9-_]*\/)(?:[a-zA-Z0-9-_\.\/]*)(?:\.json|\.yaml)"
FILENAME = WINDOWS_FILENAME if sys.platform == "win32" else UNIX_FILENAME

#: Regex to strip quotes. Group 2 will be the unquoted string.
STRIP_QUOTES = re.compile(r"^(['\"])(.*)\1$")

#: Values that match this (e.g., variants, flags) can be left unquoted in Spack output
NO_QUOTES_NEEDED = re.compile(r"^[a-zA-Z0-9,/_.\-\[\]]+$")


class SpecTokens(TokenBase):
    """Enumeration of the different token kinds of tokens in the spec grammar.

    Order of declaration is extremely important, since text containing specs is parsed with a
    single regex obtained by ``"|".join(...)`` of all the regex in the order of declaration.
    """

    # Dependency, with optional virtual assignment specifier
    START_EDGE_PROPERTIES = r"(?:[\^%]\[)"
    END_EDGE_PROPERTIES = rf"(?:\](?:\s*{VIRTUAL_ASSIGNMENT})?)"
    DEPENDENCY = rf"(?:[\^\%](?:\s*{VIRTUAL_ASSIGNMENT})?)"

    # Version
    VERSION_HASH_PAIR = rf"(?:@(?:{GIT_VERSION_PATTERN})=(?:{VERSION}))"
    GIT_VERSION = rf"@(?:{GIT_VERSION_PATTERN})"
    VERSION = rf"(?:@\s*(?:{VERSION_LIST}))"

    # Variants
    PROPAGATED_BOOL_VARIANT = rf"(?:(?:\+\+|~~|--)\s*{NAME})"
    BOOL_VARIANT = rf"(?:[~+-]\s*{NAME})"
    PROPAGATED_KEY_VALUE_PAIR = rf"(?:{NAME}:?==(?:{VALUE}|{QUOTED_VALUE}))"
    KEY_VALUE_PAIR = rf"(?:{NAME}:?=(?:{VALUE}|{QUOTED_VALUE}))"

    # FILENAME
    FILENAME = rf"(?:{FILENAME})"

    # Package name
    FULLY_QUALIFIED_PACKAGE_NAME = rf"(?:{DOTTED_IDENTIFIER})"
    UNQUALIFIED_PACKAGE_NAME = rf"(?:{IDENTIFIER}|{STAR})"

    # DAG hash
    DAG_HASH = rf"(?:/(?:{HASH}))"

    # White spaces
    WS = r"(?:\s+)"

    # Unexpected character(s)
    UNEXPECTED = r"(?:.[\s]*)"


#: Tokenizer that includes all the regexes in the SpecTokens enum
SPEC_TOKENIZER = Tokenizer(SpecTokens)


def tokenize(text: str) -> Iterator[Token]:
    """Return a token generator from the text passed as input.

    Raises:
        SpecTokenizationError: when unexpected characters are found in the text
    """
    for token in SPEC_TOKENIZER.tokenize(text):
        if token.kind == SpecTokens.UNEXPECTED:
            raise SpecTokenizationError(list(SPEC_TOKENIZER.tokenize(text)), text)
        yield token


def parseable_tokens(text: str) -> Iterator[Token]:
    """Return non-whitespace tokens from the text passed as input

    Raises:
        SpecTokenizationError: when unexpected characters are found in the text
    """
    return filter(lambda x: x.kind != SpecTokens.WS, tokenize(text))


class TokenContext:
    """Token context passed around by parsers"""

    __slots__ = "token_stream", "current_token", "next_token", "pushed_tokens"

    def __init__(self, token_stream: Iterator[Token]):
        self.token_stream = token_stream
        self.current_token = None
        self.next_token = None  # the next token to be read

        # if not empty, back of list is front of stream, and we pop from here instead.
        self.pushed_tokens: List[Token] = []

        self.advance()

    def advance(self):
        """Advance one token"""
        self.current_token = self.next_token
        if self.pushed_tokens:
            self.next_token = self.pushed_tokens.pop()
        else:
            self.next_token = next(self.token_stream, None)

    def accept(self, kind: SpecTokens):
        """If the next token is of the specified kind, advance the stream and return True.
        Otherwise return False.
        """
        if self.next_token and self.next_token.kind == kind:
            self.advance()
            return True
        return False

    def push_front(self, token=Token):
        """Push a token onto the front of the stream. Enables a bit of lookahead."""
        self.pushed_tokens.append(self.next_token)  # back of list is front of stream
        self.next_token = token

    def expect(self, *kinds: SpecTokens):
        return self.next_token and self.next_token.kind in kinds


class SpecTokenizationError(spack.error.SpecSyntaxError):
    """Syntax error in a spec string"""

    def __init__(self, tokens: List[Token], text: str):
        message = f"unexpected characters in the spec string\n{text}\n"

        underline = ""
        for token in tokens:
            is_error = token.kind == SpecTokens.UNEXPECTED
            underline += ("^" if is_error else " ") * (token.end - token.start)

        message += color.colorize(f"@*r{{{underline}}}")
        super().__init__(message)


def _warn_about_variant_after_compiler(literal_str: str, issues: List[str]):
    """Issue a warning if variant or other token is preceded by a compiler token. The warning is
    only issued if it's actionable: either we know the config file it originates from, or we have
    call site that's not internal to Spack."""
    ignore = [spack.paths.lib_path, spack.paths.bin_path]
    mark = spack.util.spack_yaml.get_mark_from_yaml_data(literal_str)
    issue_str = ", ".join(issues)
    error = f"{issue_str} in `{literal_str}`"

    # warning from config file
    if mark:
        warnings.warn(f"{mark.name}:{mark.line + 1}: {error}")
        return

    # warning from hopefully package.py
    for frame in reversed(traceback.extract_stack()):
        if frame.lineno and not any(frame.filename.startswith(path) for path in ignore):
            warnings.warn_explicit(
                error,
                category=spack.error.SpackAPIWarning,
                filename=frame.filename,
                lineno=frame.lineno,
            )
            return


def parse_virtual_assignment(context: TokenContext) -> Tuple[str]:
    """Look at subvalues and, if present, extract virtual and a push a substitute token.

    This handles things like:

    * ``^c=gcc``
    * ``^c,cxx=gcc``
    * ``%[when=+bar] c=gcc``
    * ``%[when=+bar] c,cxx=gcc``

    Virtual assignment can happen anywhere a dependency node can appear. It is
    shorthand for ``%[virtuals=c,cxx] gcc``.

    The ``virtuals=substitute`` key value pair appears in the subvalues of
    :attr:`~spack.spec_parser.SpecTokens.DEPENDENCY` and
    :attr:`~spack.spec_parser.SpecTokens.END_EDGE_PROPERTIES` tokens. We extract the virtuals and
    create a token from the substitute, which is then pushed back on the parser stream so that the
    head of the stream can be parsed like a regular node.

    Returns:
        the virtuals assigned, or None if there aren't any

    """
    assert context.current_token is not None

    subvalues = context.current_token.subvalues
    if not subvalues:
        return ()

    # build a token for the substitute that we can put back on the stream
    pkg = subvalues["substitute"]
    token_type = SpecTokens.UNQUALIFIED_PACKAGE_NAME
    if "." in pkg:
        token_type = SpecTokens.FULLY_QUALIFIED_PACKAGE_NAME
    start = context.current_token.value.index(pkg)

    token = Token(token_type, pkg, start, start + len(pkg))
    context.push_front(token)

    return tuple(subvalues["virtuals"].split(","))


class SpecParser:
    """Parse text into specs"""

    __slots__ = "literal_str", "ctx", "toolchains", "parsed_toolchains"

    def __init__(self, literal_str: str):
        self.literal_str = literal_str
        self.ctx = TokenContext(parseable_tokens(literal_str))

        # TODO: Move toolchains out of the parser, and expand them as a separate step
        self.toolchains = {}
        configuration = getattr(spack.config, "CONFIG", None)
        if configuration is not None:
            self.toolchains = configuration.get("toolchains", {})
        self.parsed_toolchains: Dict[str, "spack.spec.Spec"] = {}

    def tokens(self) -> List[Token]:
        """Return the entire list of token from the initial text. White spaces are
        filtered out.
        """
        return list(filter(lambda x: x.kind != SpecTokens.WS, tokenize(self.literal_str)))

    def next_spec(
        self, initial_spec: Optional["spack.spec.Spec"] = None
    ) -> Optional["spack.spec.Spec"]:
        """Return the next spec parsed from text.

        Args:
            initial_spec: object where to parse the spec. If None a new one
                will be created.

        Return:
            The spec that was parsed
        """
        if not self.ctx.next_token:
            return initial_spec

        def add_dependency(dep, **edge_properties):
            """wrapper around root_spec._add_dependency"""
            try:
                target_spec._add_dependency(dep, **edge_properties)
            except spack.error.SpecError as e:
                raise SpecParsingError(str(e), self.ctx.current_token, self.literal_str) from e

        if not initial_spec:
            from spack.spec import Spec

            initial_spec = Spec()
        root_spec, parser_warnings = SpecNodeParser(self.ctx, self.literal_str).parse(initial_spec)
        current_spec = root_spec
        while True:
            if self.ctx.accept(SpecTokens.START_EDGE_PROPERTIES):
                is_direct = self.ctx.current_token.value[0] == "%"

                edge_properties = EdgeAttributeParser(self.ctx, self.literal_str).parse()
                edge_properties.setdefault("virtuals", ())
                edge_properties["direct"] = is_direct
                edge_properties.setdefault("depflag", 0)

                dependency, warnings = self._parse_node(root_spec)

                if is_direct:
                    target_spec = current_spec
                    if dependency.name in LEGACY_COMPILER_TO_BUILTIN:
                        dependency.name = LEGACY_COMPILER_TO_BUILTIN[dependency.name]
                else:
                    current_spec = dependency
                    target_spec = root_spec

                parser_warnings.extend(warnings)
                add_dependency(dependency, **edge_properties)

            elif self.ctx.accept(SpecTokens.DEPENDENCY):
                is_direct = self.ctx.current_token.value[0] == "%"
                virtuals = parse_virtual_assignment(self.ctx)

                # if no virtual assignment, check for a toolchain - look ahead to find the
                # toolchain and substitute it
                if not virtuals and is_direct and self.ctx.next_token.value in self.toolchains:
                    assert self.ctx.accept(SpecTokens.UNQUALIFIED_PACKAGE_NAME)
                    try:
                        self._apply_toolchain(current_spec, self.ctx.current_token.value)
                    except spack.error.SpecError as e:
                        raise SpecParsingError(str(e), self.ctx.current_token, self.literal_str)
                    continue

                edge_properties = {"direct": is_direct, "virtuals": virtuals, "depflag": 0}
                dependency, warnings = self._parse_node(root_spec)

                if is_direct:
                    target_spec = current_spec
                    if dependency.name in LEGACY_COMPILER_TO_BUILTIN:
                        dependency.name = LEGACY_COMPILER_TO_BUILTIN[dependency.name]
                else:
                    current_spec = dependency
                    target_spec = root_spec

                parser_warnings.extend(warnings)
                add_dependency(dependency, **edge_properties)

            else:
                break

        if parser_warnings:
            _warn_about_variant_after_compiler(self.literal_str, parser_warnings)

        return root_spec

    def _parse_node(self, root_spec: "spack.spec.Spec", root: bool = True):
        dependency, parser_warnings = SpecNodeParser(self.ctx, self.literal_str).parse(root=root)
        if dependency is None:
            msg = (
                "the dependency sigil and any optional edge attributes must be followed by a "
                "package name or a node attribute (version, variant, etc.)"
            )
            raise SpecParsingError(msg, self.ctx.current_token, self.literal_str)
        if root_spec.concrete:
            raise spack.error.SpecError(root_spec, "^" + str(dependency))
        return dependency, parser_warnings

    def _apply_toolchain(self, spec: "spack.spec.Spec", name: str) -> None:
        if name not in self.parsed_toolchains:
            toolchain = self._parse_toolchain(name)
            self.parsed_toolchains[name] = toolchain

        toolchain = self.parsed_toolchains[name]
        spec.constrain(toolchain)

    def _parse_toolchain(self, name: str) -> "spack.spec.Spec":
        toolchain_config = self.toolchains[name]
        if isinstance(toolchain_config, str):
            toolchain = parse_one_or_raise(toolchain_config)
            self._ensure_all_direct_edges(toolchain)
        else:
            from spack.spec import Spec

            toolchain = Spec()
            for entry in toolchain_config:
                toolchain_part = parse_one_or_raise(entry["spec"])
                when = entry.get("when", "")
                self._ensure_all_direct_edges(toolchain_part)

                # Conditions are applied to every edge in the constraint
                for edge in toolchain_part.traverse_edges():
                    edge.when.constrain(when)
                toolchain.constrain(toolchain_part)
        return toolchain

    def _ensure_all_direct_edges(self, constraint: "spack.spec.Spec") -> None:
        for edge in constraint.traverse_edges(root=False):
            if not edge.direct:
                raise spack.error.SpecError(
                    f"cannot use '^' in toolchain definitions, and the current "
                    f"toolchain contains '{edge.format()}'"
                )

    def all_specs(self) -> List["spack.spec.Spec"]:
        """Return all the specs that remain to be parsed"""
        return list(iter(self.next_spec, None))


class SpecNodeParser:
    """Parse a single spec node from a stream of tokens"""

    __slots__ = "ctx", "has_version", "literal_str"

    def __init__(self, ctx, literal_str):
        self.ctx = ctx
        self.literal_str = literal_str
        self.has_version = False

    def parse(
        self, initial_spec: Optional["spack.spec.Spec"] = None, root: bool = True
    ) -> Tuple["spack.spec.Spec", List[str]]:
        """Parse a single spec node from a stream of tokens

        Args:
            initial_spec: object to be constructed
            root: True if we're parsing a root, False if dependency after ^ or %

        Return:
            The object passed as argument
        """
        parser_warnings: List[str] = []
        last_compiler = None

        if initial_spec is None:
            from spack.spec import Spec

            initial_spec = Spec()

        if not self.ctx.next_token or self.ctx.expect(SpecTokens.DEPENDENCY):
            return initial_spec, parser_warnings

        # If we start with a package name we have a named spec, we cannot
        # accept another package name afterwards in a node
        if self.ctx.accept(SpecTokens.UNQUALIFIED_PACKAGE_NAME):
            # if name is '*', this is an anonymous spec
            if self.ctx.current_token.value != "*":
                initial_spec.name = self.ctx.current_token.value

        elif self.ctx.accept(SpecTokens.FULLY_QUALIFIED_PACKAGE_NAME):
            parts = self.ctx.current_token.value.split(".")
            name = parts[-1]
            namespace = ".".join(parts[:-1])
            initial_spec.name = name
            initial_spec.namespace = namespace

        elif self.ctx.accept(SpecTokens.FILENAME):
            return FileParser(self.ctx).parse(initial_spec), parser_warnings

        def raise_parsing_error(string: str, cause: Optional[Exception] = None):
            """Raise a spec parsing error with token context."""
            raise SpecParsingError(string, self.ctx.current_token, self.literal_str) from cause

        def add_flag(name: str, value: Union[str, bool], propagate: bool, concrete: bool):
            """Wrapper around ``Spec._add_flag()`` that adds parser context to errors raised."""
            try:
                initial_spec._add_flag(name, value, propagate, concrete)
            except Exception as e:
                raise_parsing_error(str(e), e)

        def warn_if_after_compiler(token: str):
            """Register a warning for %compiler followed by +variant that will in the future apply
            to the compiler instead of the current root."""
            if last_compiler:
                parser_warnings.append(f"`{token}` should go before `{last_compiler}`")

        while True:
            if (
                self.ctx.accept(SpecTokens.VERSION_HASH_PAIR)
                or self.ctx.accept(SpecTokens.GIT_VERSION)
                or self.ctx.accept(SpecTokens.VERSION)
            ):
                if self.has_version:
                    raise_parsing_error("Spec cannot have multiple versions")

                initial_spec.versions = spack.version.VersionList(
                    [spack.version.from_string(self.ctx.current_token.value[1:])]
                )
                initial_spec.attach_git_version_lookup()
                self.has_version = True
                warn_if_after_compiler(self.ctx.current_token.value)

            elif self.ctx.accept(SpecTokens.BOOL_VARIANT):
                name = self.ctx.current_token.value[1:].strip()
                variant_value = self.ctx.current_token.value[0] == "+"
                add_flag(name, variant_value, propagate=False, concrete=True)
                warn_if_after_compiler(self.ctx.current_token.value)

            elif self.ctx.accept(SpecTokens.PROPAGATED_BOOL_VARIANT):
                name = self.ctx.current_token.value[2:].strip()
                variant_value = self.ctx.current_token.value[0:2] == "++"
                add_flag(name, variant_value, propagate=True, concrete=True)
                warn_if_after_compiler(self.ctx.current_token.value)

            elif self.ctx.accept(SpecTokens.KEY_VALUE_PAIR):
                name, value = self.ctx.current_token.value.split("=", maxsplit=1)
                concrete = name.endswith(":")
                if concrete:
                    name = name[:-1]

                add_flag(
                    name, strip_quotes_and_unescape(value), propagate=False, concrete=concrete
                )
                warn_if_after_compiler(self.ctx.current_token.value)

            elif self.ctx.accept(SpecTokens.PROPAGATED_KEY_VALUE_PAIR):
                name, value = self.ctx.current_token.value.split("==", maxsplit=1)
                concrete = name.endswith(":")
                if concrete:
                    name = name[:-1]
                add_flag(name, strip_quotes_and_unescape(value), propagate=True, concrete=concrete)
                warn_if_after_compiler(self.ctx.current_token.value)

            elif self.ctx.expect(SpecTokens.DAG_HASH):
                if initial_spec.abstract_hash:
                    break
                self.ctx.accept(SpecTokens.DAG_HASH)
                initial_spec.abstract_hash = self.ctx.current_token.value[1:]
                warn_if_after_compiler(self.ctx.current_token.value)

            else:
                break

        return initial_spec, parser_warnings


class FileParser:
    """Parse a single spec from a JSON or YAML file"""

    __slots__ = ("ctx",)

    def __init__(self, ctx):
        self.ctx = ctx

    def parse(self, initial_spec: "spack.spec.Spec") -> "spack.spec.Spec":
        """Parse a spec tree from a specfile.

        Args:
            initial_spec: object where to parse the spec

        Return:
            The initial_spec passed as argument, once constructed
        """
        file = pathlib.Path(self.ctx.current_token.value)

        if not file.exists():
            raise spack.error.NoSuchSpecFileError(f"No such spec file: '{file}'")

        from spack.spec import Spec

        with file.open("r", encoding="utf-8") as stream:
            if str(file).endswith(".json"):
                spec_from_file = Spec.from_json(stream)
            else:
                spec_from_file = Spec.from_yaml(stream)
        initial_spec._dup(spec_from_file)
        return initial_spec


class EdgeAttributeParser:
    __slots__ = "ctx", "literal_str"

    def __init__(self, ctx, literal_str):
        self.ctx = ctx
        self.literal_str = literal_str

    def parse(self):
        attributes = {}
        while True:
            if self.ctx.accept(SpecTokens.KEY_VALUE_PAIR):
                name, value = self.ctx.current_token.value.split("=", maxsplit=1)
                if name.endswith(":"):
                    name = name[:-1]
                value = value.strip("'\" ").split(",")
                attributes[name] = value
                if name not in ("deptypes", "virtuals", "when"):
                    msg = (
                        "the only edge attributes that are currently accepted "
                        'are "deptypes", "virtuals", and "when"'
                    )
                    raise SpecParsingError(msg, self.ctx.current_token, self.literal_str)
            # TODO: Add code to accept bool variants here as soon as use variants are implemented
            elif self.ctx.accept(SpecTokens.END_EDGE_PROPERTIES):
                virtuals = attributes.get("virtuals", ())
                virtuals += parse_virtual_assignment(self.ctx)
                attributes["virtuals"] = virtuals
                break
            else:
                msg = "unexpected token in edge attributes"
                raise SpecParsingError(msg, self.ctx.next_token, self.literal_str)

        # Turn deptypes=... to depflag representation
        if "deptypes" in attributes:
            deptype_string = attributes.pop("deptypes")
            attributes["depflag"] = spack.deptypes.canonicalize(deptype_string)

        # Turn "when" into a spec
        if "when" in attributes:
            attributes["when"] = parse_one_or_raise(attributes["when"][0])

        return attributes


def parse(text: str) -> List["spack.spec.Spec"]:
    """Parse text into a list of strings

    Args:
        text (str): text to be parsed

    Return:
        List of specs
    """
    return SpecParser(text).all_specs()


def parse_one_or_raise(
    text: str, initial_spec: Optional["spack.spec.Spec"] = None
) -> "spack.spec.Spec":
    """Parse exactly one spec from text and return it, or raise

    Args:
        text (str): text to be parsed
        initial_spec: buffer where to parse the spec. If None a new one will be created.
    """
    parser = SpecParser(text)
    result = parser.next_spec(initial_spec)
    next_token = parser.ctx.next_token

    if next_token:
        message = f"expected a single spec, but got more:\n{text}"
        underline = f"\n{' ' * next_token.start}{'^' * len(next_token.value)}"
        message += color.colorize(f"@*r{{{underline}}}")
        raise ValueError(message)

    if result is None:
        raise ValueError("expected a single spec, but got none")

    return result


class SpecParsingError(spack.error.SpecSyntaxError):
    """Error when parsing tokens"""

    def __init__(self, message, token, text):
        message += f"\n{text}"
        if token:
            underline = f"\n{' '*token.start}{'^'*(token.end - token.start)}"
            message += color.colorize(f"@*r{{{underline}}}")
        super().__init__(message)


def strip_quotes_and_unescape(string: str) -> str:
    """Remove surrounding single or double quotes from string, if present."""
    match = STRIP_QUOTES.match(string)
    if not match:
        return string

    # replace any escaped quotes with bare quotes
    quote, result = match.groups()
    return result.replace(rf"\{quote}", quote)


def quote_if_needed(value: str) -> str:
    """Add quotes around the value if it requires quotes.

    This will add quotes around the value unless it matches :data:`NO_QUOTES_NEEDED`.

    This adds:

    * single quotes by default
    * double quotes around any value that contains single quotes

    If double quotes are used, we json-escape the string. That is, we escape ``\\``,
    ``"``, and control codes.

    """
    if NO_QUOTES_NEEDED.match(value):
        return value

    return json.dumps(value) if "'" in value else f"'{value}'"
