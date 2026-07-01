"""Shared, internal helper layer for the tabs package.

These modules are tab-agnostic infrastructure consumed by two or more feature
tabs (rendering engines, descriptor->widget builders, broadcast helpers, error
and state views, etc.). The leading underscore on the *package* marks the whole
layer private; modules inside intentionally drop their own underscore prefix.
"""
