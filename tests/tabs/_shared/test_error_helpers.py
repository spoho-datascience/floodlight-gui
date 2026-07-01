"""Behavioral contracts for ``floodlight_gui.tabs._shared.error_helpers``.

``friendly_error_message`` maps a handful of exception types to plain-language
templates. The mapped templates are cosmetic copy. The one non-cosmetic contract
is the fallback for an UNMAPPED exception type: it must still yield a useful
``ExcType: message`` string rather than an empty or crashing result, so an
unexpected exception caught at a DPG callback boundary surfaces something
diagnosable to the user.

Behavioral contracts guarded here
---------------------------------
friendly_error_message
  C1  An exception of a type with no template falls back to a string carrying
      both the exception's type name and its message.
"""

from __future__ import annotations

from floodlight_gui.tabs._shared.error_helpers import friendly_error_message


def test_unmapped_exception_type_falls_back_to_type_and_message():
    """C1: an unmapped exception type yields a "TypeName: message" fallback.

    ``RuntimeError`` has no entry in the friendly-template catalog, so the
    fallback branch must produce a string containing both the type name and
    the message, matching the ``f"{type(exc).__name__}: {exc}"`` format.
    """
    exc = RuntimeError("backend exploded")
    message = friendly_error_message(exc)
    assert message == "RuntimeError: backend exploded"
