import sys
from pathlib import Path

from .common import (
    REPO_ROOT,
    _RED,
    _col,
    _print_ok,
    load_yaml,
)


def _validate_subproject_refs() -> list[tuple[Path, str]]:
    """Check all subproject: references in project.yaml files under REPO_ROOT.

    Returns a list of (yaml_path, error_message) for each invalid reference.
    Only validates subproject: entries (relative to rootprj); project: entries
    reference external OBS projects and cannot be validated locally.
    """
    errors: list[tuple[Path, str]] = []
    for yaml_path in sorted(REPO_ROOT.rglob("project.yaml")):
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


def cmd_project_verify(args) -> None:
    errors = _validate_subproject_refs()
    if errors:
        for yaml_path, msg in errors:
            rel = yaml_path.relative_to(REPO_ROOT.parent)
            print(f"error: {rel}: {msg}", file=sys.stderr)
        sys.exit(1)
    _print_ok("project verify: all subproject references are valid")
