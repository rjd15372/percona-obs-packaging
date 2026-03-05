# Claude Code Instructions

Full project context is in [`.github/copilot-instructions.md`](.github/copilot-instructions.md). Read it before working on any task.

## Development Workflow

The main script is `percona-obs` (no file extension, Python 3).
The virtualenv is at `venv/` — use it for all tooling commands.

**If `venv/` does not exist, create it and install dependencies before doing anything else:**

```sh
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

**After every code change, always run both tools in this order:**

```sh
venv/bin/black percona-obs      # format
venv/bin/pyright percona-obs    # type-check
```

Both must pass (black: "1 file reformatted" or "1 file left unchanged"; pyright: "0 errors") before considering the task done.

## Key Files

| File | Purpose |
|---|---|
| `percona-obs` | Main CLI script — the only Python file to edit |
| `requirements.txt` | Runtime + dev dependencies (`osc`, `pyyaml`, `black`, `pyright`) |
| `pyrightconfig.json` | Pyright config pointing at `venv/` |
| `root/` | All packaging content (project hierarchy mirrors OBS) |
| `.github/copilot-instructions.md` | Full project and architecture reference |
