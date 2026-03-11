#!/usr/bin/env python3
"""Poll OBS build results until all builds reach a terminal state, then report
the outcome as a GitHub commit status.

Required environment variables
-------------------------------
OBS_APIURL          OBS API URL (e.g. http://my-obs:3000)
OBS_ROOTPRJ         Root OBS project (e.g. home:Admin:percona)
GH_TOKEN            GitHub token — needs statuses:write on the repo
GITHUB_REPOSITORY   owner/repo  (set automatically by GitHub Actions)
GITHUB_SHA          Commit SHA to report on (set automatically)

Optional environment variables
-------------------------------
OBS_WEB_URL         OBS web-UI base URL; used as the status target_url.
                    Defaults to OBS_APIURL.
OBS_POLL_INTERVAL   Seconds between polls (default: 30)
OBS_POLL_TIMEOUT    Maximum seconds to wait before giving up (default: 3600)
OBS_INITIAL_WAIT    Seconds to wait before the first poll so OBS has time to
                    schedule builds after a fresh service upload (default: 30)
"""

import os
import subprocess
import sys
import time

import osc.conf

from percona_obs.cmd_build import _fetch_build_results
from percona_obs.common import REPO_ROOT, find_packages, load_yaml

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------
apiurl = os.environ["OBS_APIURL"]
rootprj = os.environ["OBS_ROOTPRJ"]
obs_web_url = os.environ.get("OBS_WEB_URL", apiurl).rstrip("/")
gh_token = os.environ.get("GH_TOKEN", "")
gh_repo = os.environ.get("GITHUB_REPOSITORY", "")
gh_sha = os.environ.get("GITHUB_SHA", "")

poll_interval = int(os.environ.get("OBS_POLL_INTERVAL", "30"))
poll_timeout = int(os.environ.get("OBS_POLL_TIMEOUT", "3600"))
initial_wait = int(os.environ.get("OBS_INITIAL_WAIT", "30"))

# ---------------------------------------------------------------------------
# Initialise osc (reads credentials from ~/.config/osc/oscrc)
# ---------------------------------------------------------------------------
osc.conf.get_config(override_apiurl=apiurl)

# ---------------------------------------------------------------------------
# Discover OBS projects from the local repo tree
# ---------------------------------------------------------------------------
root_config = load_yaml(REPO_ROOT / "project.yaml")
root_obs = root_config.get("name") or rootprj

obs_projects: set[str] = set()
for obs_project, package_path in find_packages(REPO_ROOT, root_obs):
    project_config = load_yaml(package_path.parent / "project.yaml")
    obs_name = project_config.get("name") or obs_project
    obs_projects.add(obs_name)

print(f"Monitoring {len(obs_projects)} OBS project(s): {', '.join(sorted(obs_projects))}")

# ---------------------------------------------------------------------------
# GitHub commit status helper
# ---------------------------------------------------------------------------
_STATUS_CONTEXT = "OBS Build"


def set_commit_status(state: str, description: str) -> None:
    """Post a GitHub commit status.  No-op when credentials are absent."""
    if not (gh_token and gh_repo and gh_sha):
        return
    cmd = [
        "gh", "api",
        f"repos/{gh_repo}/statuses/{gh_sha}",
        "-X", "POST",
        "-f", f"state={state}",
        "-f", f"context={_STATUS_CONTEXT}",
        "-f", f"description={description}",
        "-f", f"target_url={obs_web_url}/project/show/{rootprj}",
    ]
    subprocess.run(cmd, env={**os.environ, "GH_TOKEN": gh_token}, check=False)


# ---------------------------------------------------------------------------
# Build-state classification
# ---------------------------------------------------------------------------
NON_TERMINAL = {"building", "dispatching", "scheduled", "blocked", "finished"}
FAILURE_STATES = {"failed", "unresolvable", "broken"}

# ---------------------------------------------------------------------------
# Set pending status and wait for OBS to schedule the builds
# ---------------------------------------------------------------------------
set_commit_status("pending", "Waiting for OBS to schedule builds…")
print(f"Waiting {initial_wait}s for OBS to schedule builds…", flush=True)
time.sleep(initial_wait)

# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------
start = time.monotonic()

while True:
    state_counts: dict[str, int] = {}
    for obs_name in obs_projects:
        results, _ = _fetch_build_results(apiurl, obs_name)
        for _pkg, repos in results.items():
            for _repo, flavors in repos.items():
                for _flavor, code in flavors.items():
                    state_counts[code] = state_counts.get(code, 0) + 1

    total = sum(state_counts.values())
    still_building = sum(state_counts.get(s, 0) for s in NON_TERMINAL)
    failures = sum(state_counts.get(s, 0) for s in FAILURE_STATES)
    elapsed = int(time.monotonic() - start)

    summary = ", ".join(f"{s}={n}" for s, n in sorted(state_counts.items()))
    print(f"[{elapsed}s] {summary or 'no results yet'}", flush=True)

    if total > 0 and still_building == 0:
        break

    if time.monotonic() - start > poll_timeout:
        msg = f"Timed out after {poll_timeout // 60}min — {still_building} build(s) still running"
        print(f"ERROR: {msg}", file=sys.stderr)
        set_commit_status("error", msg)
        sys.exit(2)

    time.sleep(poll_interval)

# ---------------------------------------------------------------------------
# Report final outcome
# ---------------------------------------------------------------------------
if failures:
    msg = f"{failures} build(s) failed or unresolvable"
    print(f"FAIL: {msg}", file=sys.stderr)
    set_commit_status("failure", msg)
    sys.exit(1)

succeeded = state_counts.get("succeeded", 0)
msg = f"{succeeded} build(s) succeeded"
print(f"OK: {msg}")
set_commit_status("success", msg)
sys.exit(0)
