# Loading data

Everything in floodlight-gui starts in the **Load** tab. You can load from a
provider's own files or from one of the bundled public datasets, and you can
attach or replace the pitch at any time.

## Public datasets (no files needed)

The fastest way to get going is a bundled public dataset. Two are available:

- **EIGD-H** (handball)
- **IDSSE** (football)

1. Open the **Load** tab.
2. Pick one of the `(Public Dataset)` entries as the provider.
3. Choose a match from the combo that appears.
4. Click load.

The dataset is downloaded once and cached on your machine (under a per-user
cache directory), so later loads are instant and need no network. No file paths
are required.

## Provider files

To load your own match, pick the provider that produced it. Nine are supported:

DFL / STS, Tracab / ChyronHego, Kinexon, Opta, Second Spectrum, SkillCorner,
Sportradar, StatsPerform, and StatsBomb.

1. Open the **Load** tab and select the provider.
2. File inputs appear for the files that provider needs (for example a
   positions file plus a match-information file). Pick each file.
3. Click load.

![The Load tab](img/01-load.png)

## What loading produces

A successful load unifies the source into floodlight's core objects: `XY`
(tracking), `Events`, `Teamsheet`, and `Pitch`. Every other tab refreshes from
this data, so the period and team selectors across the app populate as soon as
a match is loaded.

## Building or replacing a pitch

The **Build / Replace Pitch** panel (collapsible, near the bottom of the Load
tab) constructs a `Pitch` from its `xlim` / `ylim` limits. Use it when a
provider does not supply a pitch, or when you want different pitch coordinates.

1. Optionally pick a template to pre-fill a provider's conventions, then edit
   any field. The limits (`xlim` / `ylim`) are the fields the renderer and the
   coordinate mapping use; `length` / `width` / `unit` / `sport` are optional.
2. Click **Create Pitch**. The new pitch is swapped into the loaded data and
   the visualization updates to match.

## Next steps

- [Transforming tracking data](transforms.md)
- [Fitting models and exporting results](models-and-export.md)
- [Visualization and export](visualization.md)
