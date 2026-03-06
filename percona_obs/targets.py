import sys
from pathlib import Path

from .common import (
    REPO_ROOT,
    _load_project_config_with_inheritance,
    find_packages,
    is_package,
    load_yaml,
    resolve_project_path,
)


def _resolve_targets(args) -> list[tuple[str, Path]]:
    """Resolve the list of (obs_project, package_path) targets from CLI args.

    args must have: rootprj, project (optional), package (optional).
    """
    recursive = not getattr(args, "non_recursive", False)

    if args.project is None:
        targets = list(find_packages(REPO_ROOT, args.rootprj, recursive=recursive))
        if not targets:
            print("error: no packages found under root", file=sys.stderr)
            sys.exit(1)
        return targets

    first_path = resolve_project_path(args.project)
    if not first_path.is_dir():
        print(f"error: '{args.project}' not found under root/", file=sys.stderr)
        sys.exit(1)

    if is_package(first_path):
        # First arg is a top-level package, not a project
        if args.package is not None:
            print(
                f"error: '{args.project}' is a package; a sub-package argument cannot be given",
                file=sys.stderr,
            )
            sys.exit(1)
        return [(args.rootprj, first_path)]

    # First arg is a project
    full_obs_project = f"{args.rootprj}:{args.project}"
    if args.package is not None:
        package_path = first_path / args.package
        if not package_path.is_dir():
            print(
                f"error: package directory not found: {package_path}", file=sys.stderr
            )
            sys.exit(1)
        if not is_package(package_path):
            print(
                f"error: '{args.package}' is not a package (no obs/ directory found inside)",
                file=sys.stderr,
            )
            sys.exit(1)
        return [(full_obs_project, package_path)]

    targets = list(find_packages(first_path, full_obs_project, recursive=recursive))
    if not targets:
        print(
            f"error: no packages found under project '{args.project}'", file=sys.stderr
        )
        sys.exit(1)
    return targets


def _iter_project_chain(obs_project: str, project_path: Path):
    """Yield (raw_obs_project, obs_project_name, path) from root down to project_path.

    Walks up from project_path to REPO_ROOT, then yields in reverse (root-first)
    so every ancestor project level is visited before the immediate project.

    raw_obs_project is the path-derived key used for deduplication.
    obs_project_name may differ if project.yaml contains a 'name' override.
    """
    chain = []
    path = project_path
    proj = obs_project
    while True:
        config = load_yaml(path / "project.yaml")
        obs_name = config.get("name") or proj
        chain.append((proj, obs_name, path))
        if path == REPO_ROOT:
            break
        if not path.is_relative_to(REPO_ROOT):
            break
        path = path.parent
        proj = proj.rsplit(":", 1)[0]
    yield from reversed(chain)


def _topo_sort_projects(
    all_projects: dict[str, tuple[str, Path]], rootprj: str
) -> list[str]:
    """Return project keys sorted so 'subproject:' dependencies come before the
    projects that reference them.  Depth (deepest first) is the secondary key
    within the same dependency tier.
    """
    local_keys = set(all_projects)
    # Map OBS project name → raw_proj key (handles 'name:' overrides in project.yaml)
    name_to_raw: dict[str, str] = {
        prj_name: raw_proj for raw_proj, (prj_name, _) in all_projects.items()
    }
    # Build dependency graph: raw_proj → set of raw_proj keys it depends on
    deps: dict[str, set[str]] = {p: set() for p in local_keys}
    for raw_proj, (_, proj_path) in all_projects.items():
        config = _load_project_config_with_inheritance(proj_path)
        for repo in config.get("repositories", []):
            for path_info in repo.get("paths", []):
                if "subproject" in path_info:
                    dep_name = f"{rootprj}:{path_info['subproject']}"
                    dep_key = name_to_raw.get(dep_name) or (
                        dep_name if dep_name in local_keys else None
                    )
                    if dep_key and dep_key != raw_proj:
                        deps[raw_proj].add(dep_key)
    # Kahn's topological sort; depth (-colon count) is the secondary tie-breaker
    in_degree = {p: len(deps[p]) for p in local_keys}
    ready = sorted(
        [p for p, d in in_degree.items() if d == 0], key=lambda x: -x.count(":")
    )
    result: list[str] = []
    while ready:
        p = ready.pop(0)
        result.append(p)
        for other in local_keys:
            if p in deps[other]:
                in_degree[other] -= 1
                if in_degree[other] == 0:
                    ready.append(other)
                    ready.sort(key=lambda x: -x.count(":"))
    # Append any remaining nodes (cycle guard — should not happen in practice)
    result.extend(
        sorted((p for p in local_keys if in_degree[p] > 0), key=lambda x: -x.count(":"))
    )
    return result
