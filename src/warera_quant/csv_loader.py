from __future__ import annotations

from pathlib import Path
import pandas as pd


def load_market_csv(path: str | Path) -> pd.DataFrame:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"CSV file not found: {file_path}")
    df = pd.read_csv(file_path)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    return df
