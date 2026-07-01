"""Behavioral contracts for the IO_REGISTRY provider partition helpers.

``registry.io`` exposes three pure key-listing helpers that drive the split
Load-tab UI: ``visible_provider_keys`` (the union, minus disabled), and the two
that partition it -- ``dataset_provider_keys`` (downloadable public datasets,
``is_dataset=True``) and ``file_provider_keys`` (local-file providers,
``is_dataset`` absent or falsy). The contract is that these two land every
visible key on exactly one side based on the real ``is_dataset`` flag, and that
the shipped dataset providers (EIGD-H, IDSSE) are the ones that land on the
dataset side.

Behavioral contracts guarded here
---------------------------------
dataset_provider_keys / file_provider_keys
  C1  The two helpers partition the visible providers: every visible key lands
      on exactly one side (no overlap, full cover), and the partition follows
      the descriptor's real ``is_dataset`` flag.
  C2  The shipped dataset descriptors (EIGD-H / IDSSE) land on the dataset
      side; the known file providers (DFL / Kinexon) land on the file side.
"""

from __future__ import annotations

from floodlight_gui.registry.io import (
    IO_REGISTRY,
    dataset_provider_keys,
    file_provider_keys,
    visible_provider_keys,
)

# Keys that ship as downloadable public datasets (is_dataset=True upstream).
_SHIPPED_DATASET_KEYS = {"eigd_h", "idsse"}
# A pair of known local-file providers used as a positive file-side anchor.
_KNOWN_FILE_KEYS = {"dfl", "kinexon"}


def test_dataset_and_file_keys_partition_visible_providers():
    """C1: dataset/file key lists partition the visible providers by is_dataset.

    The two sides must be disjoint, together cover every visible provider, and
    each side must agree with the real ``is_dataset`` flag on its descriptors.
    """
    dataset = dataset_provider_keys()
    file = file_provider_keys()
    visible = visible_provider_keys()

    dataset_set, file_set = set(dataset), set(file)

    # Disjoint and exhaustive over the visible set.
    assert dataset_set.isdisjoint(file_set)
    assert dataset_set | file_set == set(visible)

    # Each side agrees with the descriptor's real is_dataset flag.
    assert all(IO_REGISTRY[k].get("is_dataset") for k in dataset)
    assert all(not IO_REGISTRY[k].get("is_dataset") for k in file)


def test_known_providers_land_on_the_expected_side():
    """C2: shipped datasets land dataset-side; known file providers file-side."""
    dataset_set = set(dataset_provider_keys())
    file_set = set(file_provider_keys())

    assert dataset_set >= _SHIPPED_DATASET_KEYS
    assert _SHIPPED_DATASET_KEYS.isdisjoint(file_set)

    assert file_set >= _KNOWN_FILE_KEYS
    assert _KNOWN_FILE_KEYS.isdisjoint(dataset_set)
