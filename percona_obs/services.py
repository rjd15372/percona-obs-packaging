import hashlib
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from .common import (
    _REPO_DIR,
    apply_env_substitution,
    _print_ok,
    _print_pending,
    _print_same,
    logger,
)

_OBS_SERVICE_DIR = Path("/usr/lib/obs/service")
_SKIP_MODES = {"buildtime", "serveronly", "disabled"}

# Cache directories (relative to the repo root, ignored by git)
_CACHE_DIR = _REPO_DIR / ".cache"
_OBS_SCM_CACHE_DIR = _CACHE_DIR / "obs_scm"
_SVC_CACHE_DIR = _CACHE_DIR / "services"

# Matches the subdir param of obs_scm services that fetch packaging files
# from this repo (e.g. "root/percona-telemetry-agent/debian").
_PACKAGING_SUBDIR_RE = re.compile(r"root/.+/(?:debian|rpm)$")


def _has_manual_services(service_file: Path) -> bool:
    """Return True if any service in *service_file* declares mode="manual"."""
    try:
        root = ET.parse(service_file).getroot()
    except (ET.ParseError, OSError):
        return False
    return any(svc.get("mode") == "manual" for svc in root.findall("service"))


def _get_upstream_obs_scm_info(
    service_file: Path,
) -> tuple[str, str, str] | None:
    """Return (filename_prefix, url, revision) for the upstream obs_scm service.

    Packaging obs_scm services (whose subdir matches root/.+/debian or
    root/.+/rpm) are ignored.  Returns None if zero or more than one upstream
    obs_scm services are found, or if filename/url params are missing.
    """
    upstream: list[ET.Element] = []
    for svc in ET.parse(service_file).getroot().findall("service"):
        if svc.get("name") != "obs_scm":
            continue
        subdir_el = svc.find("param[@name='subdir']")
        if subdir_el is not None and _PACKAGING_SUBDIR_RE.match(
            (subdir_el.text or "").strip()
        ):
            continue
        upstream.append(svc)

    if len(upstream) != 1:
        return None

    svc = upstream[0]
    filename = ""
    url = ""
    revision = "HEAD"
    for p in svc.findall("param"):
        name = p.get("name", "")
        val = (p.text or "").strip()
        if name == "filename":
            filename = val
        elif name == "url":
            url = val
        elif name == "revision":
            revision = val

    if not filename or not url:
        return None
    return filename, url, revision


def _get_all_obs_scm_infos(
    service_file: Path,
) -> list[tuple[str, str, str]]:
    """Return (filename_prefix, url, revision) for every obs_scm service.

    Unlike _get_upstream_obs_scm_info, packaging obs_scm services (those whose
    subdir matches root/.+/debian or root/.+/rpm) are included.  Services that
    lack a filename or url param are silently skipped.
    """
    results: list[tuple[str, str, str]] = []
    for svc in ET.parse(service_file).getroot().findall("service"):
        if svc.get("name") != "obs_scm":
            continue
        filename = ""
        url = ""
        revision = "HEAD"
        for p in svc.findall("param"):
            name = p.get("name", "")
            val = (p.text or "").strip()
            if name == "filename":
                filename = val
            elif name == "url":
                url = val
            elif name == "revision":
                revision = val
        if filename and url:
            results.append((filename, url, revision))
    return results


def _find_upstream_obs_scm_filename(service_file: Path) -> str | None:
    """Return the filename param of the single upstream source obs_scm service.

    Packaging obs_scm services (whose subdir matches root/.+/debian or
    root/.+/rpm) are ignored.  Returns None — and logs a warning when
    manual services are present — if zero or more than one upstream obs_scm
    services are found.
    """
    upstream: list[ET.Element] = []
    for svc in ET.parse(service_file).getroot().findall("service"):
        if svc.get("name") != "obs_scm":
            continue
        subdir_el = svc.find("param[@name='subdir']")
        if subdir_el is not None and _PACKAGING_SUBDIR_RE.match(
            (subdir_el.text or "").strip()
        ):
            continue
        upstream.append(svc)

    if len(upstream) == 0:
        return None

    if len(upstream) > 1:
        logger.warning(
            f"{service_file}: found {len(upstream)} upstream obs_scm services; "
            "skipping service cache"
        )
        return None

    for p in upstream[0].findall("param"):
        if p.get("name") == "filename":
            return (p.text or "").strip() or None
    return None


def _read_obsinfo_commit(workdir: Path, filename_prefix: str) -> str | None:
    """Parse the commit hash from {filename_prefix}*.obsinfo in workdir.

    obs_scm writes the obsinfo as ``{filename}.obsinfo`` (no version suffix).
    The dash-version pattern is kept as a fallback for other tools that may
    include the version in the filename.
    """
    for pattern in (f"{filename_prefix}.obsinfo", f"{filename_prefix}-*.obsinfo"):
        for obsinfo in workdir.glob(pattern):
            for line in obsinfo.read_text(encoding="utf-8").splitlines():
                if line.startswith("commit:"):
                    return line.split(":", 1)[1].strip() or None
    return None


def _cache_lookup(commit_hash: str) -> list[str] | None:
    """Return cached artifact filenames for commit_hash, or None on miss."""
    entry = _SVC_CACHE_DIR / commit_hash
    if not entry.is_dir():
        return None
    files = [f.name for f in sorted(entry.iterdir()) if f.is_file()]
    return files if files else None


def _cache_store(commit_hash: str, workdir: Path, artifacts: list[str]) -> None:
    """Atomically copy artifacts from workdir into the service cache."""
    _SVC_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=_SVC_CACHE_DIR, prefix="_tmp-"))
    try:
        for name in artifacts:
            shutil.copy2(workdir / name, tmp / name)
        final = _SVC_CACHE_DIR / commit_hash
        if final.exists():
            shutil.rmtree(final)
        tmp.rename(final)
        logger.debug(f"cached {len(artifacts)} manual artifact(s) under {final}")
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def _obs_scm_cache_key(svc: ET.Element) -> str:
    """Return a stable cache key for an obs_scm service element.

    The key is the SHA256 of all sorted param name=value pairs, so any change
    to the service configuration (URL, revision, extract, versionformat, …)
    produces a different key.
    """
    parts = sorted(
        f"{p.get('name', '')}={(p.text or '').strip()}" for p in svc.findall("param")
    )
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _git_head_sha(url: str, revision: str) -> str | None:
    """Return the resolved commit SHA of revision on a remote git repository.

    Tries branch ref first, then dereferenced annotated tag, then lightweight tag.
    If revision is already a full ref path (starts with "refs/"), it is tried
    directly and takes priority over the derived patterns — this supports refs
    like "refs/pull/42/head" used by GitHub PR workflows.
    Returns None if git is unavailable, the remote is unreachable, or
    no matching ref is found.
    """
    patterns = [
        f"refs/heads/{revision}",
        f"refs/tags/{revision}^{{}}",
        f"refs/tags/{revision}",
    ]
    if revision.startswith("refs/"):
        patterns = [revision] + patterns
    try:
        logger.debug(f"resolving git revision {revision!r} on {url}")
        result = subprocess.run(
            ["git", "ls-remote", "--", url] + patterns,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        ref_map = {}
        for line in result.stdout.splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                sha, ref = parts[0].strip(), parts[1].strip()
                ref_map[ref] = sha
        # Preference order: explicit ref path, branch, dereferenced annotated tag, lightweight tag
        if revision.startswith("refs/") and revision in ref_map:
            return ref_map[revision]
        branch_ref = f"refs/heads/{revision}"
        deref_tag_ref = f"refs/tags/{revision}^{{}}"
        lightweight_tag_ref = f"refs/tags/{revision}"
        if branch_ref in ref_map:
            return ref_map[branch_ref]
        if deref_tag_ref in ref_map:
            return ref_map[deref_tag_ref]
        if lightweight_tag_ref in ref_map:
            return ref_map[lightweight_tag_ref]
    except Exception:
        return None
    return None


def _obs_scm_lookup(cache_key: str, head_sha: str) -> list[str] | None:
    """Return cached obs_scm output filenames for (cache_key, head_sha), or None."""
    entry = _OBS_SCM_CACHE_DIR / cache_key / head_sha
    if not entry.is_dir():
        return None
    files = [f.name for f in sorted(entry.iterdir()) if f.is_file()]
    return files if files else None


def _obs_scm_store(
    cache_key: str, head_sha: str, workdir: Path, filenames: list[str]
) -> None:
    """Atomically copy obs_scm output files from workdir into the obs_scm cache."""
    key_dir = _OBS_SCM_CACHE_DIR / cache_key
    key_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=key_dir, prefix="_tmp-"))
    try:
        for fname in filenames:
            shutil.copy2(workdir / fname, tmp / fname)
        final = key_dir / head_sha
        if final.exists():
            shutil.rmtree(final)
        tmp.rename(final)
        logger.debug(f"cached obs_scm output ({head_sha[:12]}) under {final}")
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def _run_local_services(
    obs_dir: Path,
    pkg_label: str = "",
    cache: bool = True,
    env_vars: dict[str, str] | None = None,
) -> tuple[Path, list[str]]:
    """Run all non-buildtime OBS services locally; return (workdir, manual_artifact_names).

    Execution is split into two phases:
      Phase 1 — non-manual services (obs_scm etc.): for each obs_scm service a
                fast git ls-remote checks whether the remote HEAD matches a
                cached run; on a hit the cached files are restored and the
                obs_scm invocation is skipped entirely.  On a miss obs_scm runs
                normally and its output is stored to the cache.
      Cache check — after phase 1 the upstream source obsinfo is read for its
                    commit hash.  On a cache hit the manual services are skipped
                    and the cached artifacts are returned immediately.
      Phase 2 — manual services (go_modules etc.): run on a cache miss.
                Results are stored to the service cache on success.

    Modes "buildtime", "serveronly", and "disabled" are skipped entirely.
    Each service binary is expected at /usr/lib/obs/service/<name>.  If a
    binary is missing a warning is logged and the service is skipped.

    If *env_vars* is provided, ``${VAR}`` tokens in the ``_service`` file are
    substituted before parsing and before the file is written into the workdir.

    The caller owns cleanup of the returned workdir.

    Raises SystemExit on service failure.
    """
    service_file = obs_dir / "_service"
    svc_text = service_file.read_text("utf-8")
    if env_vars:
        svc_text = apply_env_substitution(svc_text, env_vars, source=service_file)
    svc_root = ET.fromstring(svc_text)

    workdir = Path(tempfile.mkdtemp(prefix="percona-obs-svc-"))
    for src in obs_dir.iterdir():
        if src.is_file():
            if src.name == "_service":
                (workdir / "_service").write_text(svc_text, "utf-8")
            else:
                shutil.copy2(src, workdir / src.name)

    def _run_one(svc: ET.Element) -> list[str]:
        """Invoke one service binary; return list of generated filenames."""
        name = svc.get("name", "")
        binary = _OBS_SERVICE_DIR / name
        if not binary.exists():
            logger.warning(f"service binary not found, skipping: {binary}")
            return []

        cmd: list[str] = [str(binary)]
        for param in svc.findall("param"):
            pname = param.get("name", "")
            pval = (param.text or "").strip()
            cmd += [f"--{pname}", pval]

        outdir = Path(tempfile.mkdtemp(prefix=f"percona-obs-svc-{name}-"))
        cmd += ["--outdir", str(outdir)]

        logger.debug(f"running service: {' '.join(cmd)}")
        _print_pending(f"service {name}  {pkg_label}")
        result = subprocess.run(cmd, cwd=str(workdir), capture_output=True, text=True)
        if result.stdout:
            logger.debug(f"  stdout: {result.stdout.rstrip()}")
        if result.stderr:
            logger.debug(f"  stderr: {result.stderr.rstrip()}")

        if result.returncode != 0:
            shutil.rmtree(outdir, ignore_errors=True)
            shutil.rmtree(workdir, ignore_errors=True)
            raise SystemExit(
                f"error: service {name!r} exited with {result.returncode}:\n"
                f"  {result.stderr.strip() or '(no stderr output)'}"
            )

        generated: list[str] = []
        for out_file in sorted(outdir.iterdir()):
            if out_file.is_file():
                shutil.move(str(out_file), str(workdir / out_file.name))
                generated.append(out_file.name)
        shutil.rmtree(outdir, ignore_errors=True)
        logger.debug(f"service {name!r} produced: {generated}")
        _print_ok(f"service {name}  {pkg_label}")
        return generated

    # ── Phase 1: non-manual services (obs_scm, …) ──────────────────────────
    manual_svcs: list[ET.Element] = []
    for svc in svc_root.findall("service"):
        mode = svc.get("mode", "")
        if mode in _SKIP_MODES:
            logger.debug(f"skipping service {svc.get('name')!r} (mode={mode!r})")
            continue
        if mode == "manual":
            manual_svcs.append(svc)
            continue

        svc_name = svc.get("name", "")
        if cache and svc_name == "obs_scm":
            # Resolve the remote HEAD SHA with a fast ls-remote before
            # deciding whether to run obs_scm (full clone) or restore from cache.
            obs_url = next(
                (
                    (p.text or "").strip()
                    for p in svc.findall("param")
                    if p.get("name") == "url"
                ),
                "",
            )
            obs_rev = next(
                (
                    (p.text or "").strip()
                    for p in svc.findall("param")
                    if p.get("name") == "revision"
                ),
                "HEAD",
            )
            obs_key = _obs_scm_cache_key(svc)
            _print_pending(f"service obs_scm  {pkg_label}")
            head_sha = _git_head_sha(obs_url, obs_rev) if obs_url else None
            if head_sha:
                cached_files = _obs_scm_lookup(obs_key, head_sha)
                if cached_files is not None:
                    cache_entry = _OBS_SCM_CACHE_DIR / obs_key / head_sha
                    for fname in cached_files:
                        shutil.copy2(cache_entry / fname, workdir / fname)
                    _print_same(f"service obs_scm  {pkg_label}  (cached)")
                    logger.debug(
                        f"obs_scm cache hit ({head_sha[:12]}): "
                        f"restored {len(cached_files)} file(s)"
                    )
                    continue
            # Cache miss or ls-remote failed: run obs_scm then store output.
            generated = _run_one(svc)
            if head_sha and generated:
                try:
                    _obs_scm_store(obs_key, head_sha, workdir, generated)
                except Exception as exc:
                    logger.warning(f"obs_scm cache store failed: {exc}")
        else:
            _run_one(svc)

    # ── Cache check ─────────────────────────────────────────────────────────
    commit_hash: str | None = None
    if cache:
        upstream_filename = _find_upstream_obs_scm_filename(service_file)
        if upstream_filename:
            commit_hash = _read_obsinfo_commit(workdir, upstream_filename)
            if commit_hash:
                cached_names = _cache_lookup(commit_hash)
                if cached_names is not None:
                    for art_name in cached_names:
                        shutil.copy2(
                            _SVC_CACHE_DIR / commit_hash / art_name,
                            workdir / art_name,
                        )
                    for svc in manual_svcs:
                        _print_same(
                            f"service {svc.get('name', '?')}  {pkg_label}  (cached)"
                        )
                    logger.debug(
                        f"cache hit ({commit_hash[:12]}): "
                        f"skipping {len(manual_svcs)} manual service(s)"
                    )
                    return workdir, cached_names

    # ── Phase 2: manual services ────────────────────────────────────────────
    manual_artifacts: list[str] = []
    for svc in manual_svcs:
        manual_artifacts.extend(_run_one(svc))

    # ── Cache store ─────────────────────────────────────────────────────────
    if cache and commit_hash and manual_artifacts:
        try:
            _cache_store(commit_hash, workdir, manual_artifacts)
        except Exception as exc:
            logger.warning(f"cache store failed: {exc}")

    return workdir, manual_artifacts
