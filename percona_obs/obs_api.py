import hashlib
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path

import osc.conf
import osc.connection
import osc.core

from .common import (
    _YELLOW,
    _GREEN,
    _RED,
    _col,
    _decode_obs_response,
    _load_project_config_with_inheritance,
    _print_create,
    _print_pending,
    _print_remove,
    _print_same,
    _print_update,
    _silence_stdout,
    build_package_meta,
    build_project_meta,
    load_yaml,
    logger,
)


def _obs_api_error(
    e: urllib.error.HTTPError, context: str, body: bytes | None = None
) -> None:
    """Read the OBS HTTP error response body, log it, and exit with a friendly message.

    Parses the OBS XML error envelope (``<status code="..."><summary>...</summary></status>``)
    to show the OBS error code and summary instead of a raw traceback.  Adds an
    actionable hint for well-known error codes such as ``repo_dependency``.

    If *body* is supplied (pre-read bytes), it is used directly instead of reading
    from *e* again (which would fail since the response stream is already consumed).
    """
    if body is None:
        try:
            body = e.read()
        except Exception:
            body = b""
    decoded = body.decode("utf-8", errors="replace") if body else ""
    logger.debug(f"OBS error response body: {decoded!r}")
    code = ""
    summary = f"HTTP {e.code}: {e.reason}"
    if decoded:
        try:
            root = ET.fromstring(decoded)
            code = root.get("code", "")
            summary = (root.findtext("summary") or "").strip() or summary
        except ET.ParseError:
            summary = decoded.strip() or summary
    msg = f"{code}: {summary}" if code else summary
    if code == "repo_dependency":
        msg += "\n  hint: use --force to bypass"
    raise SystemExit(f"error {context}:\n  {msg}")


def _obs_project_exists(apiurl: str, obs_project_name: str) -> bool:
    """Return True if the OBS project already exists, False on any error (e.g. 404)."""
    try:
        osc.core.show_project_meta(apiurl, obs_project_name)
        return True
    except Exception:
        return False


def _fetch_obs_package_latest_comment(
    apiurl: str, obs_project_name: str, package_name: str
) -> str | None:
    """Return the comment from the most recent source revision of an OBS package.

    Returns None if the package does not exist, has no revisions, or on any error.
    """
    logger.debug(f"fetching revision history: {obs_project_name}/{package_name}")
    url = osc.core.makeurl(
        apiurl, ["source", obs_project_name, package_name, "_history"]
    )
    try:
        response = osc.connection.http_GET(url)
        root = ET.fromstring(response.read())
        revisions = root.findall("revision")
        if not revisions:
            return None
        last = revisions[-1]
        comment = last.findtext("comment")
        return comment.strip() if comment else None
    except Exception:
        return None


def _fetch_obs_file_md5s(
    apiurl: str, obs_project_name: str, package_name: str, expanded: bool = False
) -> dict[str, str]:
    """Return {filename: md5} for files currently stored in the OBS package source.

    Returns an empty dict if the package does not yet exist or on any error,
    which causes all local files to be uploaded unconditionally.
    """
    logger.debug(f"fetching file list: {obs_project_name}/{package_name}")
    url = osc.core.makeurl(
        apiurl,
        ["source", obs_project_name, package_name],
        query={"expand": "1"} if expanded else None,
    )
    try:
        response = osc.connection.http_GET(url)
        root = ET.fromstring(response.read())
        return {
            entry.get("name", ""): entry.get("md5", "")
            for entry in root.findall("entry")
            if entry.get("name")
        }
    except Exception:
        return {}


def _fetch_obs_file_content(
    apiurl: str,
    obs_project_name: str,
    package_name: str,
    filename: str,
    expanded: bool = False,
) -> bytes | None:
    """Fetch the raw bytes of a single file from an OBS package source.

    Returns None if the package or file does not exist or on any error.
    """
    logger.debug(f"fetching file content: {obs_project_name}/{package_name}/{filename}")
    url = osc.core.makeurl(
        apiurl,
        ["source", obs_project_name, package_name, filename],
        query={"expand": "1"} if expanded else None,
    )
    try:
        response = osc.connection.http_GET(url)
        return response.read()
    except Exception as exc:
        logger.debug(
            f"fetching file content failed: {obs_project_name}/{package_name}/{filename}: {exc}"
        )
        return None


def _fetch_obs_package_names(apiurl: str, obs_project_name: str) -> set[str]:
    """Return the set of package names currently in an OBS project.

    Returns an empty set if the project does not exist or on any error.
    """
    try:
        return {p for p in osc.core.meta_get_packagelist(apiurl, obs_project_name) if p}
    except Exception:
        return set()


def _fetch_obs_subproject_names(apiurl: str, rootprj: str) -> set[str]:
    """Return all OBS project names that are direct or indirect subprojects of rootprj.

    A subproject is any project whose name starts with '<rootprj>:'.
    Returns an empty set on any error.
    """
    try:
        prefix = rootprj + ":"
        return {
            p for p in osc.core.meta_get_project_list(apiurl) if p.startswith(prefix)
        }
    except Exception:
        return set()


def _delete_obs_package(
    apiurl: str, obs_project_name: str, package_name: str, dry_run: bool
) -> None:
    """Delete a package from OBS, or report the deletion in dry-run mode."""
    label = f"package  {obs_project_name}/{package_name}"
    if not dry_run:
        try:
            osc.core.delete_package(apiurl, obs_project_name, package_name)
        except urllib.error.HTTPError as e:
            _obs_api_error(e, f"deleting package {obs_project_name}/{package_name}")
    _print_remove(label)


def _delete_obs_project(apiurl: str, obs_project_name: str, dry_run: bool) -> None:
    """Delete all packages in an OBS project then delete the project itself.

    In dry-run mode, reports what would be deleted without making any changes.
    """
    for pkg in sorted(_fetch_obs_package_names(apiurl, obs_project_name)):
        _delete_obs_package(apiurl, obs_project_name, pkg, dry_run)
    label = f"project meta  {obs_project_name}"
    if not dry_run:
        try:
            osc.core.delete_project(apiurl, obs_project_name)
        except urllib.error.HTTPError as e:
            _obs_api_error(e, f"deleting project {obs_project_name}")
    _print_remove(label)


def _child_text(elem: ET.Element, tag: str) -> str:
    child = elem.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _project_meta_subset_equal(current_bytes: bytes, desired_xml: str) -> bool:
    """Return True if the title, description, and repositories we manage are identical.

    Ignores OBS-managed elements (person, group, lock, link) so that ACL entries
    added via the web UI do not cause spurious updates.
    """
    try:
        current = ET.fromstring(current_bytes)
        desired = ET.fromstring(desired_xml)
    except ET.ParseError:
        return False

    if _child_text(current, "title") != _child_text(desired, "title"):
        return False
    if _child_text(current, "description") != _child_text(desired, "description"):
        return False

    current_repos = {r.get("name"): r for r in current.findall("repository")}
    desired_repos = {r.get("name"): r for r in desired.findall("repository")}
    if set(current_repos) != set(desired_repos):
        return False

    for name, d_repo in desired_repos.items():
        c_repo = current_repos[name]
        d_paths = [
            (p.get("project"), p.get("repository")) for p in d_repo.findall("path")
        ]
        c_paths = [
            (p.get("project"), p.get("repository")) for p in c_repo.findall("path")
        ]
        if d_paths != c_paths:
            return False
        if [a.text for a in d_repo.findall("arch")] != [
            a.text for a in c_repo.findall("arch")
        ]:
            return False

    return True


def _package_meta_subset_equal(current_bytes: bytes, desired_xml: str) -> bool:
    """Return True if the title and description we manage are identical to what OBS has."""
    try:
        current = ET.fromstring(current_bytes)
        desired = ET.fromstring(desired_xml)
    except ET.ParseError:
        return False

    return _child_text(current, "title") == _child_text(
        desired, "title"
    ) and _child_text(current, "description") == _child_text(desired, "description")


def _edit_project_meta(
    apiurl: str, obs_project_name: str, meta: str, force: bool
) -> None:
    """Call osc.core.edit_meta for a project, auto-retrying with force=True on
    repository_access_failure.

    During bootstrapping, repository paths may reference subprojects that don't
    exist on OBS yet.  force=True bypasses the dependency check; subprojects are
    created later in the sync loop.  The retry is only attempted when force is
    False on the first call — if the caller already requested force=True the
    error is surfaced immediately.
    """
    try:
        with _silence_stdout():
            osc.core.edit_meta(
                metatype="prj",
                path_args=(obs_project_name,),
                data=[meta],
                force=force,
                apiurl=apiurl,
            )
    except urllib.error.HTTPError as e:
        err_body: bytes = b""
        try:
            err_body = e.read()
        except Exception:
            pass
        err_code = ""
        if err_body:
            try:
                err_code = ET.fromstring(err_body).get("code", "")
            except ET.ParseError:
                pass
        if not force and err_code == "repository_access_failure":
            # OBS rejects the meta because a referenced subproject doesn't
            # exist yet.  Create the project with repository <path> elements
            # stripped so OBS accepts it; the topo-sort loop will update it
            # with the full config once the subprojects have been created.
            logger.debug(
                f"repository_access_failure on create, stripping paths and retrying: {obs_project_name}"
            )
            try:
                root_el = ET.fromstring(meta)
                for repo_el in root_el.findall("repository"):
                    for path_el in repo_el.findall("path"):
                        repo_el.remove(path_el)
                stripped = ET.tostring(root_el, encoding="unicode")
            except ET.ParseError:
                stripped = meta
            try:
                with _silence_stdout():
                    osc.core.edit_meta(
                        metatype="prj",
                        path_args=(obs_project_name,),
                        data=[stripped],
                        force=False,
                        apiurl=apiurl,
                    )
            except urllib.error.HTTPError as e2:
                _obs_api_error(e2, f"writing project meta for {obs_project_name}")
        else:
            _obs_api_error(
                e, f"writing project meta for {obs_project_name}", body=err_body
            )


def _apply_project_config(
    apiurl: str,
    obs_project_name: str,
    project_path: Path,
    rootprj: str,
    force: bool = False,
    dry_run: bool = False,
) -> None:
    """Create or update OBS project metadata and build config from project.yaml.

    Skips the API call when the content already matches what OBS has, unless
    --force is given (which bypasses both the local comparison and OBS conflict checks).
    With --dry-run, read-only OBS calls are made to compute the diff but no
    changes are written.

    Prints '+' for creates, '~' for updates, '=' for unchanged resources.
    """
    project_config = _load_project_config_with_inheritance(project_path)
    meta = build_project_meta(
        obs_project_name,
        project_config.get("title", ""),
        project_config.get("description", ""),
        project_config.get("repositories", []),
        rootprj,
    )

    # --- project meta ---
    logger.debug(f"  meta XML:\n{meta}")
    current = b""
    project_meta_exists = True
    _print_pending(f"project meta  {obs_project_name}")
    try:
        logger.debug(f"fetching project meta: {obs_project_name}")
        current = _decode_obs_response(
            osc.core.show_project_meta(apiurl, obs_project_name)
        ).encode()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            project_meta_exists = False
        else:
            _obs_api_error(e, f"fetching project meta for {obs_project_name}")

    if not project_meta_exists:
        logger.debug(f"creating project meta: {obs_project_name}")
        if not dry_run:
            _edit_project_meta(apiurl, obs_project_name, meta, force=False)
        _print_create(f"project meta  {obs_project_name}")
    elif force or not _project_meta_subset_equal(current, meta):
        logger.debug(f"updating project meta: {obs_project_name}")
        if not dry_run:
            _edit_project_meta(apiurl, obs_project_name, meta, force=force)
        _print_update(f"project meta  {obs_project_name}")
    else:
        logger.debug(f"project meta unchanged: {obs_project_name}")
        _print_same(f"project meta  {obs_project_name}")

    # --- project config ---
    project_config_str = project_config.get("project-config") or ""
    current_conf = ""
    project_conf_exists = True
    _print_pending(f"project config  {obs_project_name}")
    try:
        logger.debug(f"fetching project config: {obs_project_name}")
        current_conf = _decode_obs_response(
            osc.core.show_project_conf(apiurl, obs_project_name)
        )
    except urllib.error.HTTPError as e:
        if e.code == 404:
            project_conf_exists = False
        else:
            _obs_api_error(e, f"fetching project config for {obs_project_name}")

    if not project_conf_exists:
        logger.debug(f"creating project config: {obs_project_name}")
        if not dry_run:
            try:
                with _silence_stdout():
                    osc.core.edit_meta(
                        metatype="prjconf",
                        path_args=(obs_project_name,),
                        data=[project_config_str],
                        force=False,
                        apiurl=apiurl,
                    )
            except urllib.error.HTTPError as e:
                _obs_api_error(e, f"creating project config for {obs_project_name}")
        _print_create(f"project config  {obs_project_name}")
    elif force or current_conf.strip() != project_config_str.strip():
        logger.debug(f"updating project config: {obs_project_name}")
        if not dry_run:
            try:
                with _silence_stdout():
                    osc.core.edit_meta(
                        metatype="prjconf",
                        path_args=(obs_project_name,),
                        data=[project_config_str],
                        force=force,
                        apiurl=apiurl,
                    )
            except urllib.error.HTTPError as e:
                _obs_api_error(e, f"updating project config for {obs_project_name}")
        _print_update(f"project config  {obs_project_name}")
    else:
        logger.debug(f"project config unchanged: {obs_project_name}")
        _print_same(f"project config  {obs_project_name}")


def _apply_package_config(
    apiurl: str,
    obs_project_name: str,
    package_name: str,
    package_path: Path,
    force: bool = False,
    dry_run: bool = False,
) -> None:
    """Create or update OBS package metadata from package.yaml.

    Skips the API call when the content already matches what OBS has, unless
    --force is given.  With --dry-run, read-only OBS calls are made to compute
    the diff but no changes are written.

    Prints '+' for creates, '~' for updates, '=' for unchanged resources.
    """
    package_config = load_yaml(package_path / "package.yaml")
    meta = build_package_meta(
        obs_project_name,
        package_name,
        package_config.get("title", ""),
        package_config.get("description", ""),
    )

    logger.debug(f"  package meta XML:\n{meta}")
    package_exists = True
    current = b""
    _print_pending(f"package  {obs_project_name}/{package_name}")
    try:
        logger.debug(f"fetching package meta: {obs_project_name}/{package_name}")
        current = _decode_obs_response(
            osc.core.show_package_meta(apiurl, obs_project_name, package_name)
        ).encode()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            package_exists = False
        else:
            _obs_api_error(
                e,
                f"fetching package meta for {obs_project_name}/{package_name}",
            )

    if not package_exists:
        logger.debug(f"creating package meta: {obs_project_name}/{package_name}")
        if not dry_run:
            try:
                with _silence_stdout():
                    osc.core.edit_meta(
                        metatype="pkg",
                        path_args=(obs_project_name, package_name),
                        data=[meta],
                        force=False,
                        apiurl=apiurl,
                    )
            except urllib.error.HTTPError as e:
                _obs_api_error(
                    e, f"creating package meta for {obs_project_name}/{package_name}"
                )
        _print_create(f"package  {obs_project_name}/{package_name}")
    elif force or not _package_meta_subset_equal(current, meta):
        logger.debug(f"updating package meta: {obs_project_name}/{package_name}")
        if not dry_run:
            try:
                with _silence_stdout():
                    osc.core.edit_meta(
                        metatype="pkg",
                        path_args=(obs_project_name, package_name),
                        data=[meta],
                        force=force,
                        apiurl=apiurl,
                    )
            except urllib.error.HTTPError as e:
                _obs_api_error(
                    e, f"updating package meta for {obs_project_name}/{package_name}"
                )
        _print_update(f"package  {obs_project_name}/{package_name}")
    else:
        logger.debug(f"package meta unchanged: {obs_project_name}/{package_name}")
        _print_same(f"package  {obs_project_name}/{package_name}")


def _upload_obs_files(
    apiurl: str,
    obs_project_name: str,
    package_name: str,
    obs_dir: Path,
    message: str = "",
    dry_run: bool = False,
) -> None:
    """Upload changed files from obs_dir to OBS as a single committed revision.

    Each changed file is staged with ?rev=upload (no revision created yet).
    Files present on OBS but absent locally are deleted with ?rev=upload.
    After all files are staged/deleted, a single commit is issued, optionally
    with a message. This produces one OBS source revision per sync instead of
    one per file, and skips the commit entirely when nothing changed.
    With --dry-run, read-only OBS calls are made to compute the diff but no
    files are uploaded or committed.

    Prints '=' when no files changed, '~' with a per-file breakdown for
    certain changes. In dry-run mode, OBS-only files are shown with '!' rather
    than '-' because they may be service-generated artifacts whose fate cannot
    be determined without running the services.
    """
    _print_pending(f"files  {obs_project_name}/{package_name}")
    obs_md5s = _fetch_obs_file_md5s(apiurl, obs_project_name, package_name)
    new_files: list[str] = []
    updated_files: list[str] = []
    removed: list[str] = []
    uncertain: list[str] = []
    local_files: set[str] = set()

    for filepath in sorted(obs_dir.iterdir()):
        if not filepath.is_file():
            continue
        local_files.add(filepath.name)
        local_md5 = hashlib.md5(filepath.read_bytes()).hexdigest()
        if obs_md5s.get(filepath.name) == local_md5:
            logger.debug(f"{filepath.name} unchanged (md5 match)")
            continue
        if filepath.name in obs_md5s:
            updated_files.append(filepath.name)
        else:
            new_files.append(filepath.name)
        if not dry_run:
            logger.debug(f"staging {filepath.name} → {obs_project_name}/{package_name}")
            url = osc.core.makeurl(
                apiurl,
                ["source", obs_project_name, package_name, filepath.name],
                query={"rev": "upload"},
            )
            osc.connection.http_PUT(url, file=str(filepath))

    for obs_name in sorted(obs_md5s.keys() - local_files):
        if dry_run:
            # In dry-run mode services were not run, so OBS-only files may be
            # service-generated artifacts rather than genuine deletions.
            uncertain.append(obs_name)
        else:
            removed.append(obs_name)
            logger.debug(f"deleting {obs_name} → {obs_project_name}/{package_name}")
            url = osc.core.makeurl(
                apiurl,
                ["source", obs_project_name, package_name, obs_name],
                query={"rev": "upload"},
            )
            osc.connection.http_DELETE(url)

    certain = new_files + updated_files + removed
    if not certain and not uncertain:
        logger.debug(f"no files changed: {obs_project_name}/{package_name}")
        _print_same(f"{len(local_files)} files  {obs_project_name}/{package_name}")
        return

    if not dry_run:
        commit_query: dict = {"cmd": "commit"}
        if message:
            commit_query["comment"] = message
        logger.debug(
            f"committing {len(new_files)} new, {len(updated_files)} updated, "
            f"{len(removed)} deleted: {obs_project_name}/{package_name}"
        )
        url = osc.core.makeurl(
            apiurl, ["source", obs_project_name, package_name], query=commit_query
        )
        osc.connection.http_POST(url)

    total = len(certain) + len(uncertain)
    label = f"{total} files  {obs_project_name}/{package_name}"
    if certain:
        _print_update(label)
    else:
        # Only uncertain files: package is probably unchanged but service
        # outputs on OBS cannot be verified without running the services.
        print(f"  {_col(_YELLOW, '!')} {label}")
    for name in new_files:
        print(f"      |_ {_col(_GREEN, '+')} {name}")
    for name in updated_files:
        print(f"      |_ {_col(_YELLOW, '~')} {name}")
    for name in removed:
        print(f"      |_ {_col(_RED, '-')} {name}")
    for name in uncertain:
        print(
            f"      |_ {_col(_YELLOW, '!')} {name}  (service output, skipped in dry-run)"
        )
