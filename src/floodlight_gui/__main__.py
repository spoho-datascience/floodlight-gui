"""``python -m floodlight_gui`` entrypoint."""

from __future__ import annotations


def main() -> None:
    """Console-script and ``python -m floodlight_gui`` entry point: launch the GUI."""
    from floodlight_gui import run

    run()


if __name__ == "__main__":
    main()
