"""Registry executors: the DPG-free backend that runs each registry's descriptors.

One executor module per data-registry -- descriptors live in ``registry/`` (the
"what"), these are the "how":

  - load_data        (IO_REGISTRY)        -- load + normalize provider/dataset data
  - apply_transforms (TRANSFORM_REGISTRY) -- apply a transform / XY-method to an XY
  - fit_model        (MODEL_REGISTRY)     -- resolve + fit a floodlight model
  - calculate_metric (METRICS_REGISTRY)   -- resolve + call a metric, wrap the result

DPG-free: ``import floodlight_gui.engine`` must never pull in dearpygui. The
DPG-aware tab callbacks in ``tabs/*/execute.py`` collect inputs and call these.
"""
