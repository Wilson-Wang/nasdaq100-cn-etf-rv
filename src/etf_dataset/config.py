from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ETF:
    symbol: str
    exchange: str
    name: str
    index: str

    @property
    def baostock_code(self) -> str:
        return f"{self.exchange}.{self.symbol}"


def load_universe(path: str | Path) -> list[ETF]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [ETF(**item) for item in raw]
