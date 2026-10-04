"""Unit tests for stable project dependency rules.

Builds a runtime import graph from project-owned Python source and enforces
acyclic imports, production isolation from tests, and project-private symbols.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import override

import pytest

_REPOSITORY_ROOT_CANDIDATE = Path(__file__).resolve().parents[3]
REPOSITORY_ROOT = (
    _REPOSITORY_ROOT_CANDIDATE
    if (_REPOSITORY_ROOT_CANDIDATE / "backend").is_dir()
    else Path(__file__).resolve().parents[2]
)
PROJECT_PREFIXES = ("accounts", "config", "github_scripts", "notifications", "scripts")


def source_paths(root: Path) -> tuple[Path, ...]:
    """Return every scoped production and operator Python source.

    Excludes historical migrations and test code under the same rules as the SOLID inventory while
    retaining package markers and command modules.

    Arguments:
        root: Repository-like tree containing ``src`` and ``scripts``.

    Returns:
        Sorted project-owned runtime source paths.
    """
    backend_root = root / "backend" if (root / "backend").is_dir() else root
    scopes = (backend_root / "src", backend_root / "scripts", root / ".github" / "scripts")
    return tuple(
        sorted(
            path
            for scope in scopes
            if scope.exists()
            for path in scope.rglob("*.py")
            if "migrations" not in path.parts
        )
    )


def module_name(root: Path, path: Path) -> str:
    """Return one import name from its repository path.

    Removes the source-root directory and package-marker suffix so imports can be matched against
    the same names Python uses at runtime.

    Arguments:
        root: Repository-like tree containing the path.
        path: Python source path to name.

    Returns:
        Dot-separated runtime module name.
    """
    relative = path.relative_to(root)
    if relative.parts[0] == "src":
        parts = relative.parts[1:]
    elif relative.parts[:2] == (".github", "scripts"):
        parts = ("github_scripts", *relative.parts[2:])
    else:
        parts = relative.parts
    stemmed = (*parts[:-1], path.stem)
    if stemmed[-1] == "__init__":
        stemmed = stemmed[:-1]
    return ".".join(stemmed)


class RuntimeImportVisitor(ast.NodeVisitor):
    """Collect imports reachable outside type-checking-only branches.

    Inherits from ``ast.NodeVisitor`` and tracks whether traversal is beneath an
    ``if TYPE_CHECKING`` body, excluding static typing edges from the runtime graph.

    Attributes:
        current_module: Import name of the source being visited.
        package_module: Whether the source is a package marker.
        imports: Imported module or module-member names with source line numbers.
        type_checking_depth: Number of enclosing type-checking-only bodies.

    Members:
        visit_If: Exclude the body of a ``TYPE_CHECKING`` branch.
        visit_Import: Record ordinary imports.
        visit_ImportFrom: Record module and imported-member candidates.
    """

    def __init__(self, current_module: str, *, package_module: bool) -> None:
        """Create an empty runtime import collector.

        Stores package context for relative imports and initializes the collected edge list plus
        reachable type-checking depth before AST traversal begins.

        Arguments:
            current_module: Import name of the source being visited.
            package_module: Whether the source is a package marker.

        Returns:
            None.
        """
        self.current_module = current_module
        self.package_module = package_module
        self.imports: list[tuple[str, int]] = []
        self.type_checking_depth = 0

    @override
    def visit_If(self, node: ast.If) -> None:
        """Visit an if statement while excluding a type-checking body.

        Still visits the ``else`` branch because it remains reachable at runtime when
        ``TYPE_CHECKING`` is false.

        Arguments:
            node: If statement to traverse.

        Returns:
            None.
        """
        if isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            self.type_checking_depth += 1
            for statement in node.body:
                self.visit(statement)
            self.type_checking_depth -= 1
            for statement in node.orelse:
                self.visit(statement)
            return
        self.generic_visit(node)

    @override
    def visit_Import(self, node: ast.Import) -> None:
        """Record ordinary runtime imports.

        Adds each imported module only when traversal is outside a type-checking-only branch,
        retaining the statement line for diagnostics.

        Arguments:
            node: Import statement to inspect.

        Returns:
            None.
        """
        if self.type_checking_depth == 0:
            self.imports.extend((alias.name, node.lineno) for alias in node.names)

    @override
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Record from-import module and member candidates.

        Records both the module and qualified members so graph resolution can select the most
        specific project-owned dependency.

        Arguments:
            node: From-import statement to inspect.

        Returns:
            None.
        """
        if self.type_checking_depth != 0:
            return
        module = self._resolved_from_module(node)
        if not module:
            return
        self.imports.append((module, node.lineno))
        self.imports.extend((f"{module}.{alias.name}", node.lineno) for alias in node.names)

    def _resolved_from_module(self, node: ast.ImportFrom) -> str:
        """Resolve one absolute or package-relative from-import.

        Applies Python's relative level to the current package and supports both
        ``from .module`` and ``from . import module`` forms.

        Arguments:
            node: From-import statement to resolve.

        Returns:
            Absolute imported module name, or an empty string outside a package.
        """
        if node.level == 0:
            return node.module or ""
        current_parts = self.current_module.split(".")
        package_parts = current_parts if self.package_module else current_parts[:-1]
        retained = len(package_parts) - (node.level - 1)
        if retained <= 0:
            return ""
        base = ".".join(package_parts[:retained])
        return f"{base}.{node.module}" if node.module else base


def runtime_imports(root: Path, path: Path) -> tuple[tuple[str, int], ...]:
    """Return one module's runtime import candidates.

    Parses the source and delegates reachability decisions to ``RuntimeImportVisitor``, preserving
    source lines for actionable failures.

    Arguments:
        root: Repository-like tree containing the source.
        path: Python source file to parse.

    Returns:
        Imported names paired with their statement line numbers.

    Raises:
        SyntaxError: If the source cannot be parsed.
    """
    visitor = RuntimeImportVisitor(
        module_name(root, path),
        package_module=path.name == "__init__.py",
    )
    visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    return tuple(visitor.imports)


def resolve_project_import(imported: str, modules: set[str]) -> str | None:
    """Resolve one imported name to the longest known project module.

    Prefers the most specific known prefix so importing one symbol maps to its defining module
    rather than only its top-level package.

    Arguments:
        imported: Imported module or module-member candidate.
        modules: Known project module names.

    Returns:
        Most specific matching module, or ``None`` for an external import.
    """
    candidates = [
        module for module in modules if imported == module or imported.startswith(f"{module}.")
    ]
    return max(candidates, key=len) if candidates else None


def runtime_graph(root: Path) -> dict[str, set[str]]:
    """Build the scoped project runtime import graph.

    Resolves every reachable internal import against the scoped module inventory and excludes
    external packages plus self-imports.

    Arguments:
        root: Repository-like tree to inspect.

    Returns:
        Module names mapped to direct runtime dependencies.
    """
    paths = source_paths(root)
    path_by_module = {module_name(root, path): path for path in paths}
    modules = set(path_by_module)
    graph = {module: set[str]() for module in modules}
    for module, path in path_by_module.items():
        for imported, _line in runtime_imports(root, path):
            dependency = resolve_project_import(imported, modules)
            if dependency is not None and dependency != module:
                graph[module].add(dependency)
    return graph


def reachable_modules(graph: dict[str, set[str]], start: str) -> set[str]:
    """Return every module reachable from one graph node.

    Traverses dependencies iteratively so cycle analysis remains bounded by the graph rather than
    Python recursion depth.

    Arguments:
        graph: Module dependency graph.
        start: Module whose transitive dependencies are required.

    Returns:
        Reachable module names.
    """
    reached: set[str] = set()
    pending = list(graph[start])
    while pending:
        module = pending.pop()
        if module in reached:
            continue
        reached.add(module)
        pending.extend(graph[module] - reached)
    return reached


def import_cycles(graph: dict[str, set[str]]) -> list[tuple[str, ...]]:
    """Return every strongly connected runtime component.

    Groups nodes that mutually reach one another, returning stable complete cycle membership
    without reducing architecture to a numeric score.

    Arguments:
        graph: Module dependency graph.

    Returns:
        Sorted components containing more than one module.
    """
    reachable = {module: reachable_modules(graph, module) for module in graph}
    remaining = set(graph)
    components: list[tuple[str, ...]] = []
    while remaining:
        module = min(remaining)
        component = {
            candidate
            for candidate in remaining
            if candidate in reachable[module] and module in reachable[candidate]
        }
        if component:
            components.append(tuple(sorted(component)))
            remaining -= component
        else:
            remaining.remove(module)
    return components


def forbidden_test_imports(root: Path) -> list[str]:
    """Return production imports of test modules.

    Inspects only scoped runtime edges and names each forbidden path, line, and imported module for
    direct remediation.

    Arguments:
        root: Repository-like tree to inspect.

    Returns:
        Actionable path-line diagnostics.
    """
    failures: list[str] = []
    for path in source_paths(root):
        for imported, line in runtime_imports(root, path):
            if imported == "tests" or imported.startswith("tests."):
                failures.append(f"{path.relative_to(root)}:{line} imports {imported}")
    return failures


def private_project_imports(root: Path) -> list[str]:
    """Return cross-module imports of project-private symbols.

    Reads project-owned from-imports and rejects leading-underscore members while leaving
    third-party compatibility imports outside this project rule.

    Arguments:
        root: Repository-like tree to inspect.

    Returns:
        Actionable path-line diagnostics.
    """
    failures: list[str] = []
    for path in source_paths(root):
        failures.extend(
            f"{path.relative_to(root)}:{line} imports {imported}"
            for imported, line in runtime_imports(root, path)
            if imported.startswith(PROJECT_PREFIXES)
            and imported.rsplit(".", maxsplit=1)[-1].startswith("_")
        )
    return failures


def write_module(root: Path, relative: str, source: str) -> None:
    """Write one fabricated module for a failure-focused test.

    Creates parent directories and exact source text so architecture diagnostics can be tested
    without changing the repository graph.

    Arguments:
        root: Temporary repository-like tree.
        relative: Path beneath the tree.
        source: Python source to write.

    Returns:
        None.
    """
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


@pytest.mark.unit
def test_runtime_graph_excludes_type_checking_edges(tmp_path: Path) -> None:
    """Exclude static typing imports from runtime dependency cycles.

    Builds the same model-manager shape used by the project and requires the runtime graph to retain
    only the executable edge.

    Arguments:
        tmp_path: Temporary repository-like tree.

    Returns:
        None.

    Raises:
        AssertionError: If type-only imports enter the runtime graph.
    """
    write_module(
        tmp_path,
        "src/alpha.py",
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from accounts.models import _Private\n",
    )
    write_module(tmp_path, "src/beta.py", "import alpha\n")

    assert runtime_graph(tmp_path) == {"alpha": set(), "beta": {"alpha"}}
    assert private_project_imports(tmp_path) == []


@pytest.mark.unit
def test_architecture_diagnostics_name_forbidden_imports(tmp_path: Path) -> None:
    """Name source paths, lines, and imports for both forbidden directions.

    Fabricates one test dependency and one project-private import, proving failures remain
    actionable rather than collapsing into a boolean architecture score.

    Arguments:
        tmp_path: Temporary repository-like tree.

    Returns:
        None.

    Raises:
        AssertionError: If either diagnostic omits its exact source edge.
    """
    write_module(
        tmp_path,
        "src/example.py",
        "import tests.factories\nfrom accounts.models import _private\n",
    )
    write_module(
        tmp_path,
        ".github/scripts/hook.py",
        "import tests.helpers\nfrom .helper import _private\n",
    )
    write_module(tmp_path, ".github/scripts/helper.py", "")

    relative = str(Path("src") / "example.py")
    hook = str(Path(".github") / "scripts" / "hook.py")

    assert forbidden_test_imports(tmp_path) == [
        f"{hook}:1 imports tests.helpers",
        f"{relative}:1 imports tests.factories",
    ]
    assert private_project_imports(tmp_path) == [
        f"{hook}:2 imports github_scripts.helper._private",
        f"{relative}:2 imports accounts.models._private",
    ]


@pytest.mark.unit
def test_architecture_cycle_diagnostics_name_every_member(tmp_path: Path) -> None:
    """Report complete runtime cycle membership.

    Fabricates a three-module cycle and requires one stable component, proving the enforcement names
    every module that must be disentangled.

    Arguments:
        tmp_path: Temporary repository-like tree.

    Returns:
        None.

    Raises:
        AssertionError: If cycle detection misses or fragments the component.
    """
    write_module(tmp_path, "src/alpha.py", "import beta\n")
    write_module(tmp_path, "src/beta.py", "import gamma\n")
    write_module(tmp_path, "src/gamma.py", "import alpha\n")

    assert import_cycles(runtime_graph(tmp_path)) == [("alpha", "beta", "gamma")]


@pytest.mark.unit
def test_relative_import_forms_participate_in_cycle_detection(tmp_path: Path) -> None:
    """Resolve both relative from-import forms into runtime graph edges.

    Builds a package cycle using ``from . import module`` in one direction and
    ``from .module import symbol`` in the other, requiring one complete component.

    Arguments:
        tmp_path: Temporary repository-like tree.

    Returns:
        None.

    Raises:
        AssertionError: If either relative form bypasses graph resolution.
    """
    write_module(tmp_path, "src/package/__init__.py", "")
    write_module(tmp_path, "src/package/alpha.py", "from . import beta\n")
    write_module(
        tmp_path,
        "src/package/beta.py",
        "from .alpha import value\n",
    )

    assert import_cycles(runtime_graph(tmp_path)) == [("package.alpha", "package.beta")]


@pytest.mark.unit
def test_runtime_modules_do_not_import_tests() -> None:
    """Keep production and operator code independent from test implementation.

    Runs the rule against the complete repository source inventory and requires no runtime edge
    into test helpers or fixtures.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If runtime code imports a test module.
    """
    assert forbidden_test_imports(REPOSITORY_ROOT) == []


@pytest.mark.unit
def test_project_modules_do_not_import_private_project_symbols() -> None:
    """Keep project modules behind their intended public symbols.

    Requires every cross-module project import to use a non-private name, preserving the owning
    module's intended interface.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If one project module imports another module's private symbol.
    """
    assert private_project_imports(REPOSITORY_ROOT) == []


@pytest.mark.unit
def test_runtime_import_graph_is_acyclic() -> None:
    """Keep the scoped runtime import graph free of cycles.

    Builds the complete executable graph while excluding type-only edges and requires no strongly
    connected component.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If executable project imports form a strongly connected component.
    """
    assert import_cycles(runtime_graph(REPOSITORY_ROOT)) == []
