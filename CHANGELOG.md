# Changelog

All notable changes to floodlight-gui are documented in this file. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-06-30

First public release. The initial feature set:

### Added

- Six-tab desktop GUI (Load, Inspect, Transforms, Model, Metrics,
  Visualization) over floodlight 1.2.
- Loaders for nine providers (DFL / STS, Tracab / ChyronHego, Kinexon, Opta,
  Second Spectrum, SkillCorner, Sportradar, StatsPerform, StatsBomb) plus two
  bundled public datasets (EIGD-H, IDSSE).
- Transform suite across five categories (filter, interpolation, spatial,
  temporal, permutation) with an apply / undo / reset operation stack.
- Nine models (Velocity, Acceleration, Distance, Centroid, Nearest Mate,
  Nearest Opponent, Convex Hull, Metabolic Power, Discrete Voronoi), with live
  Convex Hull and Discrete Voronoi overlays on the pitch.
- Three metrics (Approximate Entropy, Zone Aggregation, Formation Similarity).
- Interactive pitch visualization with playback, keyboard shortcuts, and frame
  export (PNG / SVG / PDF) plus optional ffmpeg-gated MP4 clip export.
- In-GUI `?` help button surfacing the upstream floodlight documentation for
  every model, transform, metric, and provider.
- CSV export for model outputs and metric results.
