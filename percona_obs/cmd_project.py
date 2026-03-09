import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

from .common import (
    REPO_ROOT,
    _BOLD,
    _DIM,
    _PROFILES_DIR,
    _RED,
    _col,
    _ENV_VAR_RE,
    _load_project_config_with_inheritance,
    build_project_meta,
    find_projects,
    is_project,
    load_yaml,
    parse_env_overrides,
    resolve_project_path,
    _print_ok,
)
from .services import _git_head_sha

_YAML_FILENAMES = {"project.yaml", "package.yaml"}
_OBS_FILENAMES = {"_service", "_aggregate", "_link"}


def _validate_subproject_refs(root: Path) -> list[tuple[Path, str]]:
    """Check all subproject: references in project.yaml files under ``root``.

    Returns a list of (yaml_path, error_message) for each invalid reference.
    Only validates subproject: entries (relative to rootprj); project: entries
    reference external OBS projects and cannot be validated locally.
    """
    errors: list[tuple[Path, str]] = []
    for yaml_path in sorted(root.rglob("project.yaml")):
        config = load_yaml(yaml_path)
        for repo in config.get("repositories", []):
            for path_info in repo.get("paths", []):
                subproject = path_info.get("subproject")
                if subproject is None:
                    continue
                # subproject uses colon notation: "builddep" → root/builddep/,
                # "ppg:17.9" → root/ppg/17.9/
                target = REPO_ROOT.joinpath(*subproject.split(":"))
                if not target.is_dir():
                    errors.append(
                        (
                            yaml_path,
                            f"subproject '{subproject}' not found "
                            f"(expected {target.relative_to(REPO_ROOT.parent)})",
                        )
                    )
    return errors


def _find_env_var_usages(root: Path) -> dict[str, list[tuple[Path, int]]]:
    """Scan project.yaml, package.yaml, and obs/{_service,_aggregate,_link} files
    under ``root`` for ${VAR} tokens.

    Returns a dict mapping variable name → list of (file_path, line_no).
    """
    usages: dict[str, list[tuple[Path, int]]] = {}

    def _scan(file_path: Path) -> None:
        with file_path.open(encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                for m in _ENV_VAR_RE.finditer(line):
                    usages.setdefault(m.group(1), []).append((file_path, lineno))

    for name in sorted(_YAML_FILENAMES):
        for fp in sorted(root.rglob(name)):
            _scan(fp)
    for name in sorted(_OBS_FILENAMES):
        for fp in sorted(root.rglob(f"obs/{name}")):
            _scan(fp)

    return usages


def _validate_env_vars(
    root: Path,
    env_vars: dict[str, str] | None,
) -> list[tuple[Path, int, str, str]]:
    """Check that every ${VAR} token under ``root`` is resolvable.

    ``env_vars`` is the merged dict of profile env + CLI overrides.  Pass
    ``None`` when no profile is active and no -e flags were given — every
    token found will be reported as an error telling the user to supply a
    profile.

    Returns a list of (file_path, line_no, var_name, error_detail).
    """
    usages = _find_env_var_usages(root)
    if not usages:
        return []

    errors: list[tuple[Path, int, str, str]] = []
    for var_name, locations in sorted(usages.items()):
        if env_vars is None:
            detail = "no profile active — re-run with -P <profile> or -e KEY:VALUE"
        elif var_name not in env_vars:
            detail = "undefined in the active profile/env"
        else:
            continue
        for fp, lineno in locations:
            errors.append((fp, lineno, var_name, detail))

    return errors


def _validate_obs_scm_revisions(
    service_files: list[Path],
) -> list[tuple[Path, str, str]]:
    """Check that every obs_scm revision in the given _service files exists remotely.

    Deduplicates by (url, revision) so shared repos only trigger one network call.

    Returns a list of (service_file, url, revision) for unresolvable revisions.
    """
    # Collect all (url, revision) pairs with the first service file that uses them.
    seen: dict[tuple[str, str], Path] = {}
    for svc_file in service_files:
        try:
            root = ET.parse(svc_file).getroot()
        except (ET.ParseError, OSError):
            continue
        for svc in root.findall("service"):
            if svc.get("name") != "obs_scm":
                continue
            url = next(
                (
                    (p.text or "").strip()
                    for p in svc.findall("param")
                    if p.get("name") == "url"
                ),
                "",
            )
            revision = next(
                (
                    (p.text or "").strip()
                    for p in svc.findall("param")
                    if p.get("name") == "revision"
                ),
                "HEAD",
            )
            if not url:
                continue
            key = (url, revision)
            if key not in seen:
                seen[key] = svc_file

    errors: list[tuple[Path, str, str]] = []
    for (url, revision), svc_file in seen.items():
        if revision.upper() == "HEAD":
            continue  # HEAD always resolves; skip the network call
        sha = _git_head_sha(url, revision)
        if sha is None:
            errors.append((svc_file, url, revision))
    return errors


def _load_profile_env(profile_name: str) -> dict[str, str]:
    """Return the env dict from .profile/<profile_name>.yaml, or exit on error."""
    profile_path = _PROFILES_DIR / f"{profile_name}.yaml"
    if not profile_path.is_file():
        raise SystemExit(f"error: profile {profile_name!r} not found: {profile_path}")
    with profile_path.open(encoding="utf-8") as fh:
        data: object = yaml.safe_load(fh) or {}
    env_list = data.get("env", []) if isinstance(data, dict) else []
    return {
        item["name"]: item["value"] if item.get("value") is not None else ""
        for item in (env_list if isinstance(env_list, list) else [])
        if isinstance(item, dict) and "name" in item
    }


def cmd_project_verify(args) -> None:
    # Resolve scan root from optional project/package scope arguments.
    if args.project:
        scan_root = resolve_project_path(args.project)
        if not scan_root.is_dir():
            print(
                f"error: project '{args.project}' not found "
                f"(expected {scan_root.relative_to(REPO_ROOT.parent)})",
                file=sys.stderr,
            )
            sys.exit(1)
        if getattr(args, "package", None):
            scan_root = scan_root / args.package
            if not scan_root.is_dir():
                print(
                    f"error: package '{args.package}' not found under '{args.project}'",
                    file=sys.stderr,
                )
                sys.exit(1)
    else:
        if getattr(args, "package", None):
            print(
                "error: a project argument is required when specifying a package",
                file=sys.stderr,
            )
            sys.exit(1)
        scan_root = REPO_ROOT

    # Build env_vars from -P profile (if any), then apply -e overrides.
    env_vars: dict[str, str] | None = None
    if args.profile:
        env_vars = _load_profile_env(args.profile)
    if args.env_overrides:
        overrides = parse_env_overrides(args.env_overrides)
        env_vars = {**(env_vars or {}), **overrides}

    ref_errors = _validate_subproject_refs(scan_root)
    env_errors = _validate_env_vars(scan_root, env_vars)
    service_files = sorted(scan_root.rglob("obs/_service"))
    scm_errors = _validate_obs_scm_revisions(service_files)

    for yaml_path, msg in ref_errors:
        rel = yaml_path.relative_to(REPO_ROOT.parent)
        print(f"error: {rel}: {msg}", file=sys.stderr)

    for file_path, lineno, var_name, detail in env_errors:
        rel = file_path.relative_to(REPO_ROOT.parent)
        print(f"error: {rel}:{lineno}: ${{{var_name}}}: {detail}", file=sys.stderr)

    for svc_file, url, revision in scm_errors:
        rel = svc_file.relative_to(REPO_ROOT.parent)
        print(
            f"error: {rel}: obs_scm revision '{revision}' not found in {url}",
            file=sys.stderr,
        )

    if ref_errors or env_errors or scm_errors:
        sys.exit(1)
    _print_ok("project verify: all checks passed")


def cmd_project_config(args) -> None:
    if not args.rootprj:
        raise SystemExit(
            "error: --rootprj is required for 'project config' "
            "(supply -R/--rootprj or use -P/--profile)"
        )

    # Build env_vars (same precedence as all other commands).
    # None means no substitution — ${VAR} tokens are shown as-is.
    env_vars: dict[str, str] | None = None
    if args.profile:
        env_vars = _load_profile_env(args.profile)
    if args.env_overrides:
        overrides = parse_env_overrides(args.env_overrides)
        env_vars = {**(env_vars or {}), **overrides}

    # Resolve scope: a single project or the whole tree.
    if args.project:
        scope_path = resolve_project_path(args.project)
        if not scope_path.is_dir():
            raise SystemExit(
                f"error: project '{args.project}' not found "
                f"(expected {scope_path.relative_to(REPO_ROOT.parent)})"
            )
        if not is_project(scope_path):
            raise SystemExit(f"error: '{args.project}' is a package, not a project")
        scope_obs_name = f"{args.rootprj}:{args.project}"
        projects = list(find_projects(scope_path, scope_obs_name))
    else:
        root_config = load_yaml(REPO_ROOT / "project.yaml")
        root_obs_name = root_config.get("name") or args.rootprj
        projects = list(find_projects(REPO_ROOT, root_obs_name))

    sep = _col(_DIM, "─" * 60)
    for obs_project_name, project_path in projects:
        project_config = _load_project_config_with_inheritance(project_path, env_vars)
        meta = build_project_meta(
            obs_project_name,
            project_config.get("title", ""),
            project_config.get("description", ""),
            project_config.get("repositories", []),
            args.rootprj,
        )
        project_config_str = (project_config.get("project-config") or "").strip()

        print(sep)
        print(_col(_BOLD, f"project meta  {obs_project_name}"))
        print(meta)
        print()
        print(_col(_BOLD, f"project config  {obs_project_name}"))
        print(project_config_str if project_config_str else _col(_DIM, "(empty)"))
        print()
