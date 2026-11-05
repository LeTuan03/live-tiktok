"""Sổ tín dụng Google Flow — đếm xem còn bao nhiêu tín dụng trước khi bấm tạo.

Flow không cho đọc số dư bằng API, nên LiveFlow giữ một sổ tay cục bộ:
bạn nhập số dư thấy trên flow.google.com một lần, sau đó mỗi clip nhận về sẽ tự trừ.
Lệch so với Flow thì bấm "Đồng bộ lại" và nhập con số thật — sổ sẽ chốt theo con số đó.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

from loguru import logger


@dataclass
class CreditEntry:
    """Một lần tiêu tín dụng."""

    ts: float
    label: str
    credits: int
    pack_id: str = ""
    job_id: str = ""


@dataclass
class CreditState:
    total: int = 1050  # số dư ban đầu người dùng nhập
    spent_entries: List[CreditEntry] = field(default_factory=list)
    synced_at: float = 0.0


class CreditLedger:
    """Đọc/ghi sổ tín dụng ra JSON trong thư mục dữ liệu của app."""

    FILENAME = "flow_credits.json"

    def __init__(self, path: Optional[Path | str] = None, default_total: int = 1050) -> None:
        if path is None:
            try:
                from app.config.settings import get_settings

                path = get_settings().data_dir / self.FILENAME
            except Exception:  # pragma: no cover - chỉ khi chạy ngoài app
                path = Path.home() / ".liveflow" / self.FILENAME
        self.path = Path(path)
        self._default_total = int(default_total)
        self.state = self._load()

    # ------------------------------------------------------------------ io
    def _load(self) -> CreditState:
        if not self.path.exists():
            return CreditState(total=self._default_total)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return CreditState(
                total=int(raw.get("total", self._default_total)),
                spent_entries=[CreditEntry(**e) for e in raw.get("spent_entries", [])],
                synced_at=float(raw.get("synced_at", 0.0)),
            )
        except Exception as e:
            logger.warning("Không đọc được sổ tín dụng Flow ({}), tạo sổ mới.", e)
            return CreditState(total=self._default_total)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "total": self.state.total,
            "synced_at": self.state.synced_at,
            "spent_entries": [asdict(e) for e in self.state.spent_entries],
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # --------------------------------------------------------------- query
    @property
    def spent(self) -> int:
        return sum(e.credits for e in self.state.spent_entries)

    @property
    def remaining(self) -> int:
        return max(0, self.state.total - self.spent)

    def history(self, limit: int = 50) -> List[CreditEntry]:
        return list(reversed(self.state.spent_entries))[:limit]

    def can_afford(self, credits: int) -> bool:
        return self.remaining >= credits

    # --------------------------------------------------------------- write
    def spend(self, label: str, credits: int, pack_id: str = "", job_id: str = "") -> int:
        """Ghi nhận đã tiêu `credits`, trả về số dư còn lại."""
        if credits <= 0:
            return self.remaining
        self.state.spent_entries.append(
            CreditEntry(ts=time.time(), label=label, credits=int(credits),
                        pack_id=pack_id, job_id=job_id)
        )
        self.save()
        logger.info("Flow: tiêu {} tín dụng cho '{}' — còn {}", credits, label, self.remaining)
        return self.remaining

    def undo(self, job_id: str = "") -> int:
        """Bỏ lần tiêu cuối (hoặc lần tiêu của một job cụ thể) — dùng khi clip bị loại."""
        if not self.state.spent_entries:
            return self.remaining
        idx = len(self.state.spent_entries) - 1
        if job_id:
            matches = [i for i, e in enumerate(self.state.spent_entries) if e.job_id == job_id]
            if not matches:
                return self.remaining
            idx = matches[-1]
        removed = self.state.spent_entries.pop(idx)
        self.save()
        logger.info("Flow: hoàn lại {} tín dụng ('{}')", removed.credits, removed.label)
        return self.remaining

    def sync_to(self, actual_remaining: int) -> int:
        """Chốt sổ theo số dư thật đọc trên flow.google.com.

        Xoá lịch sử tiêu và đặt lại tổng = số dư thật, để lần sau trừ tiếp từ mốc đúng.
        """
        self.state.total = max(0, int(actual_remaining))
        self.state.spent_entries = []
        self.state.synced_at = time.time()
        self.save()
        logger.info("Flow: đồng bộ số dư tín dụng về {}", self.state.total)
        return self.remaining
