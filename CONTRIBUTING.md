# Contributing to floodlight-gui

Thanks for your interest in improving floodlight-gui. This guide covers the most
common contribution and how to set up for local development.

New to the layout? `docs/navigation.md` is a "where does X live?" lookup that
maps a string, button, or piece of logic to the file it lives in.

## The contribution model: add a descriptor

floodlight-gui is a thin, registry-driven frontend. It does not hard-code its
providers, models, transforms, or metrics. It generates the interface from
descriptor dictionaries in `src/floodlight_gui/registry/`. Exposing a floodlight
capability in the GUI usually means **adding one descriptor**: the tab entry,
the parameter widgets, and the in-app `?` help then appear automatically.

The five registry categories:

| Category | File | Exposes |
|---|---|---|
| IO | `registry/io.py` | data providers and public datasets |
| Models | `registry/models.py` | floodlight models |
| Transforms | `registry/transforms.py` | filters, interpolation, spatial / temporal ops |
| Metrics | `registry/metrics.py` | summary metrics |
| XY-ops | `registry/transforms.py` | XY methods (slice, rotate, and similar) |

A descriptor points at the upstream floodlight callable (a `class_path` or
`function_path`) and declares its parameters. How you shape the entry is up to
you. The one rule is the **thin-frontend principle**: mirror the upstream
floodlight signature exactly, with no GUI-side renaming of methods or
parameters. The `?` button resolves its help from the upstream docstring, so a
faithful descriptor needs no GUI-side documentation of its own.

After editing a registry file, validate the shape:

```bash
python -c "from floodlight_gui.registry import validate_all; validate_all()"
```

See [docs/registry-reference.md](docs/registry-reference.md) for the full
descriptor schema (every key and its options) with an annotated example per
registry.

You can also register descriptors from your own package at runtime, without
forking, through the public helpers: `register_io_provider`, `register_model`,
`register_transform`, and `register_metric`. Each validates the descriptor,
rejects a duplicate key, inserts it, and emits a `*_REGISTRY_CHANGED` event so
the tabs refresh.

### What it looks like

Once the descriptor validates, launch the app: your entry appears in the
relevant tab's picker, its parameters render as widgets, and the `?` button
shows the floodlight documentation. No tab code changes are required.

## Development setup

1. Fork and clone the repository.
2. Install [Poetry](https://python-poetry.org/docs/#installation) 2.0 or newer
   (the project uses the Poetry build backend and a `poetry.lock`).
3. Install the project with all extras and the dev tools. Poetry creates and
   manages the virtualenv for you:

   ```bash
   poetry install --all-extras
   ```

   This installs the app in editable mode, the `video` extra (MP4 clip export
   via a bundled ffmpeg), and the dev group (pytest, ruff). Drop `--all-extras`
   if you do not need video export; everything else still works.

4. Run the app, the tests, and the linter through the managed environment:

   ```bash
   poetry run floodlight-gui        # or: poetry run python -m floodlight_gui
   poetry run pytest
   poetry run ruff check src tests
   ```

   `poetry run <cmd>` executes inside the project's virtualenv. To drop into an
   activated shell instead, use `poetry env activate` (Poetry 2.x).

## Code style

- **Lint with ruff**: a clean `poetry run ruff check src tests` is expected on
  every change (this is the CI gate). Automatic formatting via `ruff format` is
  not enforced yet, so do not run it on unrelated files.
- **Optional pre-commit hook**: install pre-commit (`pip install pre-commit`),
  then run `pre-commit install`. It runs the same ruff lint gate as CI on each
  commit (see `.pre-commit-config.yaml`).
- **The backend stays DPG-free**: `import floodlight_gui` and its `core`,
  `registry`, and `engine` packages must not import dearpygui. Only the `tabs/`
  layer touches the GUI toolkit.
- **Cross-tab communication goes through the EventBus**, never direct calls
  between tabs.
- **Docstrings are NumPy-style**
- Naming: `snake_case` functions, `PascalCase` classes, a leading underscore for
  private helpers, and `UPPERCASE` for registries and constants.

## Running tests

```bash
poetry run pytest              # full suite
poetry run ruff check src tests  # lint
```

Adding or changing a descriptor is covered by the parametrized registry tests
(`test_model_registry.py`, `test_transform_registry.py`,
`test_metrics_registry.py`), which check each descriptor's declared parameters
against the upstream floodlight signature.

## Submitting a change

This project uses a `main` / `develop` / feature-branch model. `main` holds
released code and `develop` is the integration branch; both are protected, so
all changes arrive by pull request.

1. Branch from `develop` (e.g. `feat/my-change`).
2. Make your change and keep the suite green (`poetry run pytest`) and
   `poetry run ruff check src tests` clean.
3. Use conventional-commit messages (`feat: ...`, `fix: ...`, `docs: ...`,
   `refactor: ...`).
4. Open a pull request **into `develop`** describing the change. If you added or
   modified a descriptor, say so and note the upstream floodlight version it
   targets.

Maintainers merge `develop` into `main` and tag `vX.Y.Z` to cut a release.

## License

By contributing you agree that your contributions are licensed under the
project's [MIT License](LICENSE).
