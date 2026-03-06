import xml.etree.ElementTree as ET
from pathlib import Path

import osc.conf
import osc.connection
import osc.core

from .common import (
    _BOLD,
    _CYAN,
    _DIM,
    _GREEN,
    _RED,
    _YELLOW,
    _col,
    _print_ok,
    _print_pending,
    is_package,
    load_yaml,
    logger,
    resolve_project_path,
    REPO_ROOT,
)
from .targets import _resolve_targets

# ---------------------------------------------------------------------------
# Build-status helpers
# ---------------------------------------------------------------------------

# Priority for collapsing multi-arch results: lower index = more actionable.
_STATUS_PRIORITY: dict[str, int] = {
    "failed": 0,
    "unresolvable": 1,
    "broken": 2,
    "building": 3,
    "dispatching": 4,
    "scheduled": 5,
    "blocked": 6,
    "finished": 7,
    "succeeded": 8,
    "excluded": 9,
    "disabled": 10,
    "unknown": 11,
}


def _status_indicator(code: str) -> str:
    """Return a colored symbol + status-code string for an OBS build status."""
    if code == "succeeded":
        return f"{_col(_GREEN, '✔')} {_col(_GREEN, code)}"
    if code in ("failed", "unresolvable", "broken"):
        return f"{_col(_RED, '✗')} {_col(_RED, code)}"
    if code in ("building", "dispatching"):
        return f"{_col(_CYAN, '●')} {_col(_CYAN, code)}"
    if code in ("scheduled", "blocked"):
        return f"{_col(_YELLOW, '◌')} {_col(_YELLOW, code)}"
    if code in ("excluded", "disabled"):
        return f"{_col(_DIM, '–')} {_col(_DIM, code)}"
    return f"{_col(_DIM, '?')} {_col(_DIM, code)}"


def _fetch_build_results(
    apiurl: str, obs_project_name: str
) -> tuple[dict[str, dict[str, dict[str, str]]], dict[str, dict[str, str]]]:
    """Fetch build results for all packages in an OBS project.

    Returns (results, succeeded_archs) where:
      results: {base_package: {repository: {flavor: status_code}}}
        - base_package  -- package name without any ':flavor' multibuild suffix
        - repository    -- OBS repository name (e.g. 'RockyLinux_9')
        - flavor        -- multibuild flavor string; '' for non-multibuild packages
        - status_code   -- e.g. 'succeeded', 'failed', 'building'
      succeeded_archs: {base_package: {repository: arch}}
        One representative arch per (package, repository) that has succeeded,
        used to query build history for version information.

    When the same (package, repository, flavor) has results for multiple
    architectures, the highest-priority (most actionable) status is kept.

    For multibuild packages the bare (no-flavor) entry is dropped when it
    carries only 'excluded' or 'disabled' and flavored entries are present —
    it is OBS scaffolding that adds no useful information.

    Returns ({}, {}) on any error.
    """
    url = osc.core.makeurl(apiurl, ["build", obs_project_name, "_result"])
    try:
        response = osc.connection.http_GET(url)
        result_root = ET.fromstring(response.read())
    except Exception:
        return {}, {}

    results: dict[str, dict[str, dict[str, str]]] = {}
    succeeded_archs: dict[str, dict[str, str]] = {}
    for result_elem in result_root.findall("result"):
        repo = result_elem.get("repository", "")
        arch = result_elem.get("arch", "")
        for status_elem in result_elem.findall("status"):
            full_pkg = status_elem.get("package", "")
            code = status_elem.get("code", "unknown")
            base_pkg, _, flavor = full_pkg.partition(":")
            repo_map = results.setdefault(base_pkg, {}).setdefault(repo, {})
            # Keep highest-priority (lowest index) status when arches differ.
            if flavor not in repo_map or (
                _STATUS_PRIORITY.get(code, 99)
                < _STATUS_PRIORITY.get(repo_map[flavor], 99)
            ):
                repo_map[flavor] = code
            if code == "succeeded":
                succeeded_archs.setdefault(base_pkg, {}).setdefault(repo, arch)

    for pkg_repos in results.values():
        for flavor_map in pkg_repos.values():
            if "" in flavor_map and len(flavor_map) > 1:
                if flavor_map.get("") in ("excluded", "disabled"):
                    del flavor_map[""]

    return results, succeeded_archs


def _version_from_binary(filename: str) -> str | None:
    """Extract version-release from a binary package filename.

    Supports RPM (name-version-release.arch.rpm) and
    DEB (name_version_arch.deb) naming conventions.
    """
    if filename.endswith(".rpm") and not filename.endswith(".src.rpm"):
        # name-version-release.arch.rpm → strip arch.rpm, split off last two -
        base = filename.rsplit(".", 2)  # ['name-ver-rel', 'arch', 'rpm']
        if len(base) == 3:
            chunks = base[0].rsplit("-", 2)
            if len(chunks) == 3:
                return f"{chunks[1]}-{chunks[2]}"
    elif filename.endswith(".deb"):
        # name_version_arch.deb
        parts = filename[:-4].split("_", 2)
        if len(parts) >= 2:
            return parts[1]
    return None


def _fetch_pkg_versrel(
    apiurl: str, obs_project: str, repo: str, arch: str, pkg: str
) -> str | None:
    """Return the version-release (e.g. '3.5.26-6.1') by inspecting the binary list."""
    url = osc.core.makeurl(apiurl, ["build", obs_project, repo, arch, pkg])
    try:
        response = osc.connection.http_GET(url)
        root = ET.fromstring(response.read())
        for binary in root.findall("binary"):
            ver = _version_from_binary(binary.get("filename", ""))
            if ver:
                return ver
    except Exception:
        pass
    return None


def _print_pkg_repos(
    repo_results: dict[str, dict[str, str]],
    prefix: str,
    versions: dict[str, str] | None = None,
) -> None:
    """Print per-repo (and per-flavor) build status lines for a single package."""
    repos = sorted(repo_results)
    if not repos:
        print(f"{prefix}└── {_col(_DIM, '─ not in OBS')}")
        return
    for j, repo in enumerate(repos):
        repo_is_last = j == len(repos) - 1
        repo_conn = "└── " if repo_is_last else "├── "
        flavor_map = repo_results[repo]
        all_flavors = sorted(flavor_map.items())
        unique_codes = {c for _, c in all_flavors}
        ver = f"  {_col(_DIM, versions[repo])}" if versions and repo in versions else ""
        if len(unique_codes) == 1:
            code = next(iter(unique_codes))
            tag = ""
            if all_flavors and all_flavors[0][0]:
                tags = " ".join(f"[:{f}]" for f, _ in all_flavors)
                tag = f"  {_col(_DIM, tags)}"
            print(f"{prefix}{repo_conn}{repo:<22} {_status_indicator(code)}{tag}{ver}")
        else:
            # Flavors have different statuses — expand each as its own sub-line.
            print(f"{prefix}{repo_conn}{repo}")
            sub = prefix + ("    " if repo_is_last else "│   ")
            for k, (flavor, code) in enumerate(all_flavors):
                flav_conn = "└── " if k == len(all_flavors) - 1 else "├── "
                print(f"{sub}{flav_conn}:{flavor:<14} {_status_indicator(code)}{ver}")


def _print_project_tree(
    path: Path,
    obs_project: str,
    target_set: set[tuple[str, str]],
    all_results: dict[str, dict[str, dict[str, dict[str, str]]]],
    all_versions: dict[str, dict[str, dict[str, str]]],
    prefix: str,
    is_last: bool,
    is_root: bool = False,
) -> None:
    """Recursively print the project / package tree with build status lines."""
    config = load_yaml(path / "project.yaml")
    obs_name = config.get("name") or obs_project

    if is_root:
        print(_col(_BOLD, obs_name))
        child_prefix = ""
    else:
        display = obs_project.rsplit(":", 1)[-1]
        connector = "└── " if is_last else "├── "
        print(f"{prefix}{connector}{_col(_BOLD, display)}")
        child_prefix = prefix + ("    " if is_last else "│   ")

    children = sorted(d for d in path.iterdir() if d.is_dir())
    items: list[tuple[str, Path]] = []
    for child in children:
        if is_package(child):
            if (obs_name, child.name) in target_set:
                items.append(("package", child))
        else:
            child_raw = f"{obs_project}:{child.name}"
            child_cfg = load_yaml(child / "project.yaml")
            child_obs = child_cfg.get("name") or child_raw
            has_targets = any(
                proj == child_obs or proj.startswith(f"{child_obs}:")
                for proj, _ in target_set
            )
            if has_targets:
                items.append(("project", child))

    for i, (kind, child) in enumerate(items):
        child_is_last = i == len(items) - 1
        pkg_prefix = child_prefix + ("    " if child_is_last else "│   ")
        if kind == "project":
            child_raw = f"{obs_project}:{child.name}"
            _print_project_tree(
                child,
                child_raw,
                target_set,
                all_results,
                all_versions,
                child_prefix,
                child_is_last,
            )
        else:
            pkg_name = child.name
            pkg_conn = "└── " if child_is_last else "├── "
            print(f"{child_prefix}{pkg_conn}{pkg_name}")
            repo_results = all_results.get(obs_name, {}).get(pkg_name, {})
            pkg_versions = all_versions.get(obs_name, {}).get(pkg_name)
            _print_pkg_repos(repo_results, pkg_prefix, pkg_versions)


def cmd_build_trigger(args):
    """Trigger an OBS service run for one or more packages.

    Supported call forms:
      build trigger                        — trigger services for all packages under root/
      build trigger <project>              — trigger services for all packages under a project
      build trigger <top-level-package>    — trigger service for a single top-level package
      build trigger <project> <package>    — trigger service for a single package under a project
    """
    targets = _resolve_targets(args)
    apiurl = osc.conf.config["apiurl"]

    for obs_project, package_path in targets:
        project_path = package_path.parent
        project_config = load_yaml(project_path / "project.yaml")
        obs_project_name = project_config.get("name") or obs_project
        logger.debug(f"triggering service run: {obs_project_name}/{package_path.name}")
        _print_pending(f"trigger  {obs_project_name}/{package_path.name}")
        osc.core.runservice(apiurl, obs_project_name, package_path.name)
        _print_ok(f"trigger  {obs_project_name}/{package_path.name}")

    _print_ok("build trigger done")


def cmd_build_status(args):
    """Show build status of packages as a tree.

    Supported call forms:
      build status                        — status for all packages under root/
      build status <project>              — status for all packages under a project
      build status <top-level-package>    — status for a single top-level package
      build status <project> <package>    — status for a single package
    """
    targets = _resolve_targets(args)
    apiurl = osc.conf.config["apiurl"]

    # Build target_set with resolved OBS project names.
    target_set: set[tuple[str, str]] = set()
    unique_obs_projects: set[str] = set()
    for obs_project, package_path in targets:
        project_config = load_yaml(package_path.parent / "project.yaml")
        obs_name = project_config.get("name") or obs_project
        target_set.add((obs_name, package_path.name))
        unique_obs_projects.add(obs_name)

    # Fetch build results per OBS project.
    all_results: dict[str, dict[str, dict[str, dict[str, str]]]] = {}
    all_succeeded_archs: dict[str, dict[str, dict[str, str]]] = {}
    for obs_name in unique_obs_projects:
        logger.debug(f"fetching build results: {obs_name}")
        results, succeeded_archs = _fetch_build_results(apiurl, obs_name)
        all_results[obs_name] = results
        all_succeeded_archs[obs_name] = succeeded_archs

    # Fetch versrel for each succeeded (package, repository).
    all_versions: dict[str, dict[str, dict[str, str]]] = {}
    for obs_name, pkg_archs in all_succeeded_archs.items():
        for pkg, repo_archs in pkg_archs.items():
            for repo, arch in repo_archs.items():
                versrel = _fetch_pkg_versrel(apiurl, obs_name, repo, arch, pkg)
                if versrel:
                    all_versions.setdefault(obs_name, {}).setdefault(pkg, {})[
                        repo
                    ] = versrel

    # Determine the tree root: use the specified project subtree when given,
    # otherwise show the full tree from the root project.
    if args.project is not None:
        proj_path = resolve_project_path(args.project)
        if proj_path.is_dir() and not is_package(proj_path):
            root_path = proj_path
            root_raw = f"{args.rootprj}:{args.project}"
        else:
            root_path = REPO_ROOT
            root_raw = args.rootprj
    else:
        root_path = REPO_ROOT
        root_raw = args.rootprj

    _print_project_tree(
        root_path,
        root_raw,
        target_set,
        all_results,
        all_versions,
        "",
        False,
        is_root=True,
    )
