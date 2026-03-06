import argparse

import yaml

from .common import (
    _BOLD,
    _DIM,
    _PROFILES_DIR,
    _RED,
    _col,
    _print_create,
    _print_ok,
    _print_update,
)


def _load_profile(name: str) -> dict[str, str]:
    """Load OBS connection settings from .profile/<name>.yaml.

    Returns a dict with keys matching the CLI long option names (e.g.
    ``apiurl``, ``rootprj``).  Raises SystemExit if the file is missing.
    """
    path = _PROFILES_DIR / f"{name}.yaml"
    if not path.is_file():
        available: list[str] = (
            sorted(p.stem for p in _PROFILES_DIR.glob("*.yaml"))
            if _PROFILES_DIR.is_dir()
            else []
        )
        hint = (
            f"  Available profiles: {', '.join(available)}"
            if available
            else "  No profiles found in .profile/ — create one first."
        )
        raise SystemExit(f"error: profile {name!r} not found: {path}\n{hint}")
    with path.open(encoding="utf-8") as fh:
        data: object = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise SystemExit(f"error: profile {path} is empty or not a YAML mapping")
    return {k: str(v) for k, v in data.items() if v is not None}


def cmd_profile_create(args: argparse.Namespace) -> None:
    if not args.apiurl:
        raise SystemExit("error: -A/--apiurl is required for 'profile create'")
    if not args.rootprj:
        raise SystemExit("error: -R/--rootprj is required for 'profile create'")
    _PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    path = _PROFILES_DIR / f"{args.name}.yaml"
    exists = path.is_file()
    data = {"apiurl": args.apiurl, "rootprj": args.rootprj}
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(data, fh, default_flow_style=False, allow_unicode=True)
    label = f"{args.name}  ({path})"
    if exists:
        _print_update(label)
    else:
        _print_create(label)
    _print_ok(f"profile create: {args.name}")


def cmd_profile_list(args: argparse.Namespace) -> None:
    if not _PROFILES_DIR.is_dir():
        print(
            "  No profiles found. "
            "Create one with: percona-obs -A <url> -R <prj> profile create <name>"
        )
        return
    profiles = sorted(_PROFILES_DIR.glob("*.yaml"))
    if not profiles:
        print(
            "  No profiles found. "
            "Create one with: percona-obs -A <url> -R <prj> profile create <name>"
        )
        return
    for path in profiles:
        print(f"  {_col(_BOLD, path.stem)}")
        try:
            with path.open(encoding="utf-8") as fh:
                data: object = yaml.safe_load(fh)
            if isinstance(data, dict):
                for key, val in data.items():
                    print(f"    {_col(_DIM, key + ':')}  {val}")
        except Exception:
            print(f"    {_col(_RED, '(error reading file)')}")
