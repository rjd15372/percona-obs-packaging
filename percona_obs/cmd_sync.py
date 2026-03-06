import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import osc.conf

from .git_utils import _check_git_clean, _generate_sync_message
from .obs_api import (
    _apply_package_config,
    _apply_project_config,
    _delete_obs_package,
    _delete_obs_project,
    _fetch_obs_package_names,
    _fetch_obs_subproject_names,
    _obs_project_exists,
    _upload_obs_files,
)
from .common import (
    _print_ok,
    _print_update,
    load_yaml,
)
from .services import _has_manual_services, _run_local_services
from .targets import _iter_project_chain, _resolve_targets, _topo_sort_projects


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
    if not args.dirty:
        _check_git_clean()
    targets = _resolve_targets(args)
    apiurl = osc.conf.config["apiurl"]
    seen_projects: set = set()
    local_project_names: set[str] = set()
    local_packages_by_project: dict[str, set[str]] = {}
    dry_run_obs = args.dry_run or args.dry_run_remote

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
                        combined = Path(tempfile.mkdtemp(prefix="percona-obs-upload-"))
                        try:
                            for f in obs_dir.iterdir():
                                if f.is_file():
                                    shutil.copy2(f, combined / f.name)
                            for art_name in manual_artifacts:
                                shutil.copy2(workdir / art_name, combined / art_name)
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
