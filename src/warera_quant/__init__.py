"""WarEra Market Guide - aggressive decision-support tool for WarEra in-game market trading."""

__version__ = "0.1.0"

def main() -> None:
    from .cli import main as run
    run()

__all__ = ["main", "__version__"]