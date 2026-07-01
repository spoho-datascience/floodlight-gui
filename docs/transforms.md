# Transforming tracking data

The **Transforms** tab applies floodlight's signal-processing and spatial
transforms to the loaded `XY` data. Transforms are organised into five
categories, shown as a tab bar at the top of the tab:

- **Filter**: Butterworth Low-Pass, Savitzky-Golay Low-Pass, Wiener Filter,
  FIR Low-Pass, Kalman Filter.
- **Interpolation**: Linear, Polynomial, and Spline interpolation (fill missing
  frames).
- **Spatial**: Subtract Centroid, Min-Max Normalize Formation, Translate,
  Scale, Reflect, Rotate.
- **Temporal**: Resample.
- **Permutation**: Assign Roles.

## Applying a transform

1. Open the **Transforms** tab and pick a category, then an operation within it.
2. Choose the period and team the transform should apply to.
3. Set the operation's parameters (each parameter has a `?` button that shows
   the upstream floodlight documentation).
4. Click **Apply**.

The transform applies to the active `XY` for the selected period and team. To
apply across every period and team at once, use the broadcast ("All") scope.

## The operation stack

Transforms accumulate as a stack rather than overwriting the data:

- **Apply** pushes a new operation onto the stack.
- **Undo** removes the most recent operation.
- **Reset** clears the stack and returns to the pristine `XY`.

The transformed `XY` is always replayed from the original data through the
current stack, so the stack is the single source of truth. The results view
shows the operations currently applied. To see the transformed values in full,
open the **Inspect** tab.

## Next steps

- [Fitting models and exporting results](models-and-export.md)
- [Visualization and export](visualization.md)
