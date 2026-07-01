# Navigation: where does X live?

A reverse index for "I want to find a string / a button / some logic, where is it?" For a full directory listing see `.planning/codebase/STRUCTURE.md`; this file is the concept-to-location lookup.

Every module starts with a one-line docstring stating its job, so once you are in roughly the right folder, the top line of each file confirms the rest.

## The one rule that answers most questions

The code is three layers. Knowing which layer a thing belongs to usually tells you the folder before you search:

| You are looking for... | Layer | Folder |
|------------------------|------------------------|------------------------|
| A **user-visible string** (provider name, tooltip, model/metric/transform display name, description, dropdown options) | the "what" | `registry/` |
| A **UI color** | theme | `theme.py` |
| A **button, widget, or static label** | the UI | `tabs/<tab>/` |
| The **logic** behind a load / fit / compute / transform (no DPG) | the "how" | `engine/` |
| **Loaded data** or cross-tab **events** | backend state | `core/` |

So: a *string* is almost always in `registry/` (or `theme.py` for colors, or the tab's `controls.py` if it is a hardcoded label). A *button* is built in the tab's `controls.py` and its click handler lives in that tab's `execute.py` (or, for the Load tab, in the relevant section file). The *math* is in `engine/`.

## Find it fast (concept to file)

| I want to find... | It lives in |
|------------------------------------|------------------------------------|
| Provider display names, tooltips, file-picker config | `registry/io.py` |
| Model / metric / transform display names, params, descriptions | `registry/models.py` · `metrics.py` · `transforms.py` |
| What a registry descriptor's fields mean | `docs/registry-reference.md` |
| UI colors / semantic color roles | `theme.py` |
| A tab's widget layout and static label text | `tabs/<tab>/controls.py` |
| What a run/fit/compute/apply button does | `tabs/<tab>/execute.py` |
| The actual compute / load / fit / transform code | `engine/*.py` (one file per registry) |
| Cross-tab events and the event names | `core/event_bus.py` |
| The loaded-data container | `core/data_store.py` |
| Player identity (xID / jersey / name) mapping | `core/player_mapping.py` |
| Period display-name to internal-key vocabulary | `core/periods.py` |
| The `?` help button and the upstream-docstring popup | `tabs/_shared/help_popup.py` · `core/help/` |
| The paginated results table widget | `tabs/_shared/array_view.py` |
| CSV export of results | `tabs/_shared/export_action.py` · `tabs/_shared/model_export.py` |

## Same filename, different tab

Registry tabs share a deliberately uniform 7-file skeleton, so `controls.py`, `execute.py`, `select.py`, `params.py`, `results.py`, and `state.py` each appear in several tabs. The folder is the disambiguator. In VS Code, a Ctrl-P search shows the folder beside each hit (`execute.py  tabs/metrics`), and enabling editor tab folder labels makes open tabs unambiguous too.

| File (in any tab) | Role |
|------------------------------------|------------------------------------|
| `controls.py` | builds the tab layout + wires EventBus subscriptions |
| `select.py` | resolves *what to operate on* (the descriptor pick + the period/team data slice) |
| `params.py` | builds the param/input widgets and collects them into call kwargs |
| `execute.py` | the producer: the run / fit / compute / apply button callbacks |
| `results.py` | renders the results view |
| `state.py` | the tab's module-level mutable state |

Other tabs decompose by their own concerns instead of this skeleton: `tabs/inspect/` (`collect` · `leaves` · `sections` · `render`), `tabs/load/` (the section files above), and `tabs/visualization/` (the real-time render loop and its satellites).

## Searching

When the map does not cover something, search content rather than filenames. The docstrings make this reliable: ripgrep the user-facing text or a tag string, for example `rg "Import Dataset"` lands on the dataset section, and `rg '"display_name"'` lands on the registry descriptors.