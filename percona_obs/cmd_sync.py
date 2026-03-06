import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import osc.conf

from .cmd_profile import _load_profile
from .cmd_project import _validate_subproject_refs
from .common import (
    REPO_ROOT,
    _build_aggregate_xml,
    _print_aggregate,
    _print_ok,
    _print_update,
    load_yaml,
    logger,
)
from .git_utils import (
    _check_git_clean,
    _generate_sync_message,
    _has_package_changes_since,
)
from .obs_api import (
    _apply_package_config,
    _apply_project_config,
    _delete_obs_package,
    _delete_obs_project,
    _fetch_obs_package_latest_comment,
    _fetch_obs_package_names,
    _fetch_obs_subproject_names,
    _obs_project_exists,
    _upload_obs_files,
)
from .services import _has_manual_services, _run_local_services
from .targets import _iter_project_chain, _resolve_targets, _topo_sort_projects

# Matches the standard sync commit message: sync: <branch>@<sha> (<detail>)
_SYNC_MSG_RE = re.compile(r"^sync: [^@]+@([0-9a-f]+) \((.+)\)$")


def _resolve_branch_decision(
    apiurl: str,
    branch_project: str,
    package_name: str,
    package_path: Path,
) -> bool:
    """Return True if the package should be aggregated from branch_project.

    Aggregation is safe only when:
    - The package exists in branch_project with a clean 'sync:' revision message,
    - The message contains a git SHA that can be found in local history, and
    - The package directory has no commits since that SHA.
    """
    label = f"{branch_project}/{package_name}"
    comment = _fetch_obs_package_latest_comment(apiurl, branch_project, package_name)
    if not comment:
        logger.debug(
            f"branch decision: sync  {label}  (no revision comment in branch project)"
        )
        return False

    m = _SYNC_MSG_RE.match(comment)
    if not m:
        logger.debug(
            f"branch decision: sync  {label}  (comment is not a sync message: {comment!r})"
        )
        return False  # not a sync message (e.g. branch: or manual commit)

    short_sha = m.group(1)
    details = m.group(2)
    if details.startswith("local changes on"):
        logger.debug(
            f"branch decision: sync  {label}  (branch was synced dirty at {short_sha})"
        )
        return False  # dirty sync — cannot trust the SHA

    changed = _has_package_changes_since(short_sha, package_path)
    if changed:
        logger.debug(
            f"branch decision: sync  {label}  (local changes since {short_sha})"
        )
    else:
        logger.debug(
            f"branch decision: aggregate  {label}  (no changes since {short_sha})"
        )
    return not changed


def cmd_sync(args):
    """Sync local packaging files to OBS, creating or updating projects and packages.

    Supported call forms:
      sync                        — sync everything under root/
      sync <project>              — sync all packages under a project
      sync <top-level-package>    — sync a single package with no project grouping
      sync <project> <package>    — sync a single package under a project
    """
    if args.project_only and args.package is not None:
        print(
            "error: --project-only cannot be combined with a package argument",
            file=sys.stderr,
        )
        sys.exit(1)

    # Validate local project configuration before making any API calls.
    ref_errors = _validate_subproject_refs()
    if ref_errors:
        for yaml_path, msg in ref_errors:
            rel = yaml_path.relative_to(REPO_ROOT.parent)
            print(f"error: {rel}: {msg}", file=sys.stderr)
        sys.exit(1)

    if not args.dirty:
        _check_git_clean()
    targets = _resolve_targets(args)
    apiurl = osc.conf.config["apiurl"]

    # Resolve --branch-from profile (same OBS instance, different root project).
    branch_rootprj: str | None = None
    if args.branch_from:
        branch_profile = _load_profile(args.branch_from)
        branch_apiurl = branch_profile.get("apiurl", "")
        branch_rootprj = branch_profile.get("rootprj", "")
        if branch_apiurl and branch_apiurl.rstrip("/") != apiurl.rstrip("/"):
            print(
                f"error: --branch-from profile '{args.branch_from}' uses a different "
                f"OBS instance ({branch_apiurl}) than the current profile ({apiurl}). "
                "Cross-instance branching is not supported.",
                file=sys.stderr,
            )
            sys.exit(1)
    seen_projects: set = set()
    local_project_names: set[str] = set()
    local_packages_by_project: dict[str, set[str]] = {}
    dry_run_obs = args.dry_run or args.dry_run_remote

    # Always create the root project first. OBS requires parent projects to exist
    # before any subproject can be created, so this must happen before the topo
    # sort loop (which may order subprojects ahead of the root due to repository
    # path dependencies).
    root_config = load_yaml(REPO_ROOT / "project.yaml")
    root_obs_name = root_config.get("name") or args.rootprj
    _apply_project_config(
        apiurl,
        root_obs_name,
        REPO_ROOT,
        args.rootprj,
        force=args.force,
        dry_run=dry_run_obs,
    )
    seen_projects.add(args.rootprj)

    # Pre-pass: apply all project configs in dependency order — projects referenced
    # via 'subproject:' first, then deeper subprojects before their parents.
    # This ensures any project referenced in a repository path already exists on
    # OBS before the project that references it is written, avoiding
    # repository_access_failure errors.
    if args.package is None:
        all_projects: dict[str, tuple[str, Path]] = {}
        for obs_project, package_path in targets:
            for raw_proj, prj_name, proj_path in _iter_project_chain(
                obs_project, package_path.parent
            ):
                local_project_names.add(prj_name)
                if raw_proj not in all_projects:
                    all_projects[raw_proj] = (prj_name, proj_path)
        for raw_proj in _topo_sort_projects(all_projects, args.rootprj):
            prj_name, proj_path = all_projects[raw_proj]
            _apply_project_config(
                apiurl,
                prj_name,
                proj_path,
                args.rootprj,
                force=args.force,
                dry_run=dry_run_obs,
            )
            seen_projects.add(raw_proj)

    if args.project_only:
        if args.dry_run:
            suffix = " (dry run)"
        elif args.dry_run_remote:
            suffix = " (dry run: remote)"
        else:
            suffix = ""
        _print_ok(f"sync successful{suffix}")
        return

    for obs_project, package_path in targets:
        project_path = package_path.parent
        project_config = load_yaml(project_path / "project.yaml")
        obs_project_name = project_config.get("name") or obs_project

        if args.package is not None:
            # Single-package target: ensure the project hierarchy exists on OBS.
            # If the immediate project is missing, apply the full chain in
            # dependency order so projects referenced via 'subproject:' are
            # created before the projects that reference them.
            if not _obs_project_exists(apiurl, obs_project_name):
                chain: dict[str, tuple[str, Path]] = {}
                for raw_proj, prj_name, proj_path in _iter_project_chain(
                    obs_project, project_path
                ):
                    local_project_names.add(prj_name)
                    if raw_proj not in chain:
                        chain[raw_proj] = (prj_name, proj_path)
                for raw_proj in _topo_sort_projects(chain, args.rootprj):
                    prj_name, proj_path = chain[raw_proj]
                    if raw_proj not in seen_projects:
                        _apply_project_config(
                            apiurl,
                            prj_name,
                            proj_path,
                            args.rootprj,
                            force=args.force,
                            dry_run=dry_run_obs,
                        )
                        seen_projects.add(raw_proj)

        _apply_package_config(
            apiurl,
            obs_project_name,
            package_path.name,
            package_path,
            force=args.force,
            dry_run=dry_run_obs,
        )
        local_packages_by_project.setdefault(obs_project_name, set()).add(
            package_path.name
        )

        obs_dir = package_path / "obs"
        if obs_dir.is_dir():
            # Determine whether to aggregate or sync sources.
            # Derive the corresponding branch project name by substituting the
            # branch rootprj prefix for the current rootprj prefix.
            use_aggregate = False
            branch_project = ""
            if branch_rootprj:
                if obs_project_name == args.rootprj:
                    branch_project = branch_rootprj
                elif obs_project_name.startswith(args.rootprj + ":"):
                    suffix = obs_project_name[len(args.rootprj) :]
                    branch_project = branch_rootprj + suffix
                else:
                    branch_project = obs_project_name
                use_aggregate = _resolve_branch_decision(
                    apiurl, branch_project, package_path.name, package_path
                )

            if use_aggregate:
                # Upload only an _aggregate file pointing to the branch project.
                agg_message = (
                    f"branch: {args.branch_from} "
                    f"({branch_project}/{package_path.name})"
                )
                agg_xml = _build_aggregate_xml(branch_project, package_path.name)
                agg_dir = Path(tempfile.mkdtemp(prefix="percona-obs-agg-"))
                try:
                    (agg_dir / "_aggregate").write_text(agg_xml, encoding="utf-8")
                    _upload_obs_files(
                        apiurl,
                        obs_project_name,
                        package_path.name,
                        agg_dir,
                        message=agg_message,
                        dry_run=dry_run_obs,
                    )
                finally:
                    shutil.rmtree(agg_dir, ignore_errors=True)
                _print_aggregate(
                    f"{obs_project_name}/{package_path.name}"
                    f"  → {branch_project}/{package_path.name}"
                )
            else:
                message = args.message or _generate_sync_message(args.dirty)
                service_file = obs_dir / "_service"
                run_services = (
                    not args.no_services
                    and service_file.is_file()
                    and _has_manual_services(service_file)
                )
                if run_services:
                    if args.dry_run and not args.dry_run_remote:
                        # Pure dry-run: cannot run services; show service names and
                        # report obs/ diff as-is.
                        svc_root = ET.parse(service_file).getroot()
                        for svc in svc_root.findall("service"):
                            if svc.get("mode") == "manual":
                                svc_name = svc.get("name", "?")
                                _print_update(
                                    f"service {svc_name}  {obs_project_name}/{package_path.name}"
                                )
                        _upload_obs_files(
                            apiurl,
                            obs_project_name,
                            package_path.name,
                            obs_dir,
                            message=message,
                            dry_run=True,
                        )
                    else:
                        workdir, manual_artifacts = _run_local_services(
                            obs_dir,
                            pkg_label=f"{obs_project_name}/{package_path.name}",
                            cache=not args.no_cache,
                        )
                        try:
                            combined = Path(
                                tempfile.mkdtemp(prefix="percona-obs-upload-")
                            )
                            try:
                                for f in obs_dir.iterdir():
                                    if f.is_file():
                                        shutil.copy2(f, combined / f.name)
                                for art_name in manual_artifacts:
                                    shutil.copy2(
                                        workdir / art_name, combined / art_name
                                    )
                                _upload_obs_files(
                                    apiurl,
                                    obs_project_name,
                                    package_path.name,
                                    combined,
                                    message=message,
                                    dry_run=dry_run_obs,
                                )
                            finally:
                                shutil.rmtree(combined, ignore_errors=True)
                        finally:
                            shutil.rmtree(workdir, ignore_errors=True)
                else:
                    _upload_obs_files(
                        apiurl,
                        obs_project_name,
                        package_path.name,
                        obs_dir,
                        message=message,
                        dry_run=dry_run_obs,
                    )

    # --- orphan cleanup ---
    # Remove packages on OBS that no longer exist locally, but only when the
    # full package set of a project was processed (not a single-package sync).
    if args.package is None:
        for proj_name, local_pkgs in local_packages_by_project.items():
            obs_pkgs = _fetch_obs_package_names(apiurl, proj_name)
            for orphan in sorted(obs_pkgs - local_pkgs):
                _delete_obs_package(apiurl, proj_name, orphan, dry_run_obs)

    # Remove subprojects on OBS that no longer exist locally, but only when
    # the full tree was processed (not a single-project or single-package sync).
    # Delete deepest subprojects first so parents are empty before deletion.
    if args.project is None:
        obs_subprojects = _fetch_obs_subproject_names(apiurl, args.rootprj)
        orphan_projects = obs_subprojects - local_project_names
        for orphan_proj in sorted(orphan_projects, key=lambda x: -x.count(":")):
            _delete_obs_project(apiurl, orphan_proj, dry_run_obs)

    if args.dry_run:
        suffix = " (dry run)"
    elif args.dry_run_remote:
        suffix = " (dry run: remote)"
    else:
        suffix = ""
    _print_ok(f"sync successful{suffix}")
