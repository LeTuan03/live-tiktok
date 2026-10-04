"""Log Exporter & Support Package Bundler (§6 P6).

Packages logs, system doctor report, and session stats into a sanitized ZIP archive for diagnostics.
Sanitizes stream keys, passwords, and API tokens to protect user credentials.
"""

from __future__ import annotations

import io
import os
import re
import time
import zipfile
from pathlib import Path
from typing import Optional

from loguru import logger

from tools.doctor import LiveFlowDoctor

DEFAULT_LOGS_DIR = Path("logs")
DEFAULT_EXPORTS_DIR = Path("storage/exports")

# Patterns to sanitize before packaging logs
SENSITIVE_PATTERNS = [
    (re.compile(r"(live_\w{20,})", re.IGNORECASE), "live_***REDACTED***"),
    (re.compile(r"(AIzaSy[A-Za-z0-9_-]{33})", re.IGNORECASE), "AIzaSy***REDACTED_API_KEY***"),
    (re.compile(r"(password[\"':\s=]+)([^\"'\s,]+)", re.IGNORECASE), r"\1***REDACTED***"),
    (re.compile(r"(stream_key[\"':\s=]+)([^\"'\s,]+)", re.IGNORECASE), r"\1***REDACTED***"),
    (re.compile(r"(token[\"':\s=]+)([^\"'\s,]+)", re.IGNORECASE), r"\1***REDACTED***"),
]


def sanitize_log_content(text: str) -> str:
    """Mask credentials and secrets from text logs."""
    sanitized = text
    for pattern, repl in SENSITIVE_PATTERNS:
        sanitized = pattern.sub(repl, sanitized)
    return sanitized


class LogExporter:
    """Exports sanitized logs and system diagnostic summary into a compressed ZIP file."""

    def __init__(
        self,
        logs_dir: Optional[Path | str] = None,
        export_dir: Optional[Path | str] = None,
    ) -> None:
        self.logs_dir = Path(logs_dir or DEFAULT_LOGS_DIR)
        self.export_dir = Path(export_dir or DEFAULT_EXPORTS_DIR)
        self.export_dir.mkdir(parents=True, exist_ok=True)

    def export_package(self, output_zip: Optional[Path | str] = None) -> Path:
        """Create sanitized ZIP containing logs and doctor report."""
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        if output_zip is None:
            target = self.export_dir / f"liveflow_log_package_{timestamp}.zip"
        else:
            target = Path(output_zip)

        target.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            # 1. Add Doctor diagnostic report
            doc = LiveFlowDoctor()
            results = doc.run_all_checks()
            doc_lines = [
                "LIVEFLOW SYSTEM DOCTOR REPORT",
                f"Generated at: {time.strftime('%Y-%m-%d %H:%M:%S')}",
                "=" * 50,
            ]
            for r in results:
                doc_lines.append(f"[{r.status}] {r.name}: {r.message}")
                if r.detail:
                    doc_lines.append(f"   Detail: {r.detail}")
            zf.writestr("doctor_report.txt", "\n".join(doc_lines))

            # 2. Add sanitized log files
            if self.logs_dir.exists():
                for log_file in self.logs_dir.glob("*.log*"):
                    try:
                        raw = log_file.read_text(encoding="utf-8", errors="replace")
                        sanitized = sanitize_log_content(raw)
                        zf.writestr(f"logs/{log_file.name}", sanitized)
                    except Exception as e:
                        logger.warning("Không thể nén file log {}: {}", log_file, e)

            # 3. Add reports if existing
            reports_dir = Path("storage/reports")
            if reports_dir.exists():
                for rep in reports_dir.glob("*.md"):
                    try:
                        zf.writestr(f"reports/{rep.name}", rep.read_text(encoding="utf-8", errors="replace"))
                    except Exception:
                        pass

        logger.info("Đã xuất gói hỗ trợ thành công: {} ({} bytes)", target, target.stat().st_size)
        return target
