import argparse
import logging
import sys

import osc.conf

from .cmd_build import cmd_build_status, cmd_build_trigger
from .cmd_profile import _load_profile, cmd_profile_create, cmd_profile_list
from .cmd_sync import cmd_sync
from .common import _DIM, _col, logger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="percona-obs",
        description="Manage Percona packages on an OBS instance.",
    )
    parser.add_argument(
        "-A",
        "--apiurl",
        metavar="URL",
        help="OBS API URL (e.g. http://my-obs-instance.local:8000)",
    )
    parser.add_argument(
        "-R",
        "--rootprj",
        metavar="PROJECT",
        help="OBS root project under which all projects are deployed (e.g. home:username)",
    )
    parser.add_argument(
        "-P",
        "--profile",
        metavar="NAME",
        help="Load OBS connection settings from .profile/<NAME>.yaml "
        "(sets apiurl and rootprj; explicit -A/-R override the profile values).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Print debug-level log messages (API calls, unchanged items) to stdout.",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    subparsers.required = True

    sync_parser = subparsers.add_parser(
        "sync",
        help="Sync local packaging files to OBS, creating or updating projects and packages.",
    )
    sync_parser.add_argument(
        "project",
        nargs="?",
        default=None,
        help="Project name (colon notation, e.g. ppg:17.9) or top-level package name. "
        "If omitted, all packages and projects under root/ are synced.",
    )
    sync_parser.add_argument(
        "package",
        nargs="?",
        default=None,
        help="Package name to sync. If omitted, all packages and subprojects under the project are synced.",
    )
    sync_parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Force update even if OBS reports a conflict.",
    )
    sync_parser.add_argument(
        "--dirty",
        action="store_true",
        default=False,
        help="Skip the git clean check (allow uncommitted changes or an unpushed HEAD).",
    )
    sync_parser.add_argument(
        "-m",
        "--message",
        default="",
        metavar="MSG",
        help="Commit message recorded in the OBS source revision when files are uploaded.",
    )
    sync_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Simulate actions without writing anything to OBS.",
    )
    sync_parser.add_argument(
        "--dry-run-remote",
        action="store_true",
        default=False,
        help="Run local services but skip all OBS write calls. Use to verify services work without committing to OBS.",
    )
    sync_parser.add_argument(
        "--no-services",
        action="store_true",
        default=False,
        help="Skip running local OBS services (mode=manual). Upload obs/ files as-is.",
    )
    sync_parser.add_argument(
        "--no-cache",
        action="store_true",
        default=False,
        help="Disable the service artifact cache; always run manual services (e.g. go_modules).",
    )
    sync_parser.add_argument(
        "--non-recursive",
        action="store_true",
        default=False,
        dest="non_recursive",
        help="Only sync packages directly under the specified project; do not descend into subprojects.",
    )
    sync_parser.add_argument(
        "--project-only",
        action="store_true",
        default=False,
        dest="project_only",
        help="Only sync project configuration (meta and build config); skip all package syncing.",
    )
    sync_parser.set_defaults(func=cmd_sync)

    build_parser_ = subparsers.add_parser(
        "build",
        help="Trigger OBS builds or check their status.",
    )
    build_subparsers = build_parser_.add_subparsers(
        dest="build_command", metavar="<subcommand>"
    )
    build_subparsers.required = True

    _build_project_help = (
        "Project name (colon notation, e.g. ppg:17.9) or top-level package name. "
        "If omitted, all packages under root/ are targeted."
    )
    _build_package_help = (
        "Package name. If omitted, all packages under the project are targeted."
    )

    build_trigger_parser = build_subparsers.add_parser(
        "trigger",
        help="Trigger an OBS service run for one or more packages.",
    )
    build_trigger_parser.add_argument(
        "project", nargs="?", default=None, help=_build_project_help
    )
    build_trigger_parser.add_argument(
        "package", nargs="?", default=None, help=_build_package_help
    )
    build_trigger_parser.set_defaults(func=cmd_build_trigger)

    build_status_parser = build_subparsers.add_parser(
        "status",
        help="Show build status of packages as a tree.",
    )
    build_status_parser.add_argument(
        "project", nargs="?", default=None, help=_build_project_help
    )
    build_status_parser.add_argument(
        "package", nargs="?", default=None, help=_build_package_help
    )
    build_status_parser.set_defaults(func=cmd_build_status)

    profile_parser = subparsers.add_parser(
        "profile",
        help="Manage connection profiles.",
    )
    profile_subparsers = profile_parser.add_subparsers(
        dest="profile_command", metavar="<subcommand>"
    )
    profile_subparsers.required = True

    profile_create_parser = profile_subparsers.add_parser(
        "create",
        help="Create or update a connection profile from -A and -R values.",
    )
    profile_create_parser.add_argument(
        "name",
        help="Profile name (saved as .profile/<name>.yaml).",
    )
    profile_create_parser.set_defaults(func=cmd_profile_create)

    profile_list_parser = profile_subparsers.add_parser(
        "list",
        help="List available connection profiles and their settings.",
    )
    profile_list_parser.set_defaults(func=cmd_profile_list)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # Resolve profile values; explicit -A / -R always take precedence.
    if args.profile:
        profile = _load_profile(args.profile)
        if not args.apiurl:
            args.apiurl = profile.get("apiurl")
        if not args.rootprj:
            args.rootprj = profile.get("rootprj")

    if args.command != "profile" and not args.rootprj:
        parser.error(
            "rootprj is required: supply -R/--rootprj or use -P/--profile <name>"
        )

    if args.verbose:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(f"  {_col(_DIM, '·')} %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

    if args.command != "profile":
        osc.conf.get_config(override_apiurl=args.apiurl)

    args.func(args)
