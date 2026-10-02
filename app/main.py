"""LiveFlow AI Studio — Application entry point."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

from loguru import logger


def _fatal(title: str, message: str) -> None:
    """Show a fatal error to the user (dialog when Qt is available, else console)."""
    print(f"\n❌ {title}\n{message}\n", file=sys.stderr)
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        _app = QApplication.instance() or QApplication(sys.argv)  # keep alive while the dialog shows
        QMessageBox.critical(None, title, message)
    except Exception:  # pragma: no cover
        pass


def _install_excepthook() -> None:
    """Log uncaught exceptions and show a dialog instead of silently dying."""

    def handler(exc_type, exc_value, exc_tb):  # noqa: ANN001
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logger.error("Uncaught exception:\n{}", text)
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            if QApplication.instance() is not None:
                QMessageBox.critical(
                    None,
                    "Lỗi không mong muốn",
                    "Ứng dụng gặp lỗi nhưng vẫn tiếp tục chạy. Chi tiết đã được ghi vào thư mục log.\n\n"
                    f"{exc_type.__name__}: {exc_value}",
                )
        except Exception:  # pragma: no cover
            pass

    sys.excepthook = handler


def main() -> int:
    """Main entry point for the application."""
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("❌ PySide6 chưa được cài đặt! Chạy INSTALL.bat hoặc: pip install -r requirements.txt")
        return 1

    from app.config.settings import get_settings

    settings = get_settings()

    from app.core.logging import setup_logging

    setup_logging(log_dir=settings.effective_log_dir, log_level=settings.log_level, debug=settings.debug)
    _install_excepthook()

    logger.info("=" * 60)
    logger.info("LiveFlow AI Studio v{} starting...", settings.app_version)
    logger.info("Data dir: {}", settings.data_dir)
    logger.info("=" * 60)

    from app.bootstrap import check_critical_failures, initialize_app_directories, run_all_checks

    initialize_app_directories(settings.data_dir)

    logger.info("Checking dependencies...")
    dep_results = run_all_checks()
    critical_failures = check_critical_failures(dep_results)
    if critical_failures:
        lines = "\n".join(f.status_text for f in critical_failures)
        _fatal("Thiếu thành phần bắt buộc", lines)
        return 1

    # High-DPI policy must be set before QApplication is created
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(settings.app_name)
    app.setApplicationVersion(settings.app_version)
    app.setOrganizationName("LiveFlow")
    app.setQuitOnLastWindowClosed(False)  # tray mode keeps the app alive

    from app.database.session import close_database, init_database

    try:
        logger.info("Initializing database...")
        init_database(settings.db_url, echo=settings.debug)
    except Exception as e:  # noqa: BLE001
        logger.exception("Database initialization failed")
        _fatal(
            "Không thể mở cơ sở dữ liệu",
            f"{e}\n\nĐường dẫn: {Path(settings.db_url.replace('sqlite:///', ''))}\n"
            "Hãy kiểm tra quyền ghi thư mục hoặc đóng ứng dụng khác đang mở file này.",
        )
        return 1

    # Resolve FFmpeg from the saved setting (if any) so metadata extraction works
    try:
        from app.database.session import get_db
        from app.services.settings_service import KEY_FFMPEG_DIR, SettingsService
        from app.utils.ffmpeg_locator import apply_to_settings

        with get_db() as db:
            ffmpeg_dir = SettingsService(db).get_setting(KEY_FFMPEG_DIR, "")
        ffmpeg, ffprobe = apply_to_settings(ffmpeg_dir)
        logger.info("FFmpeg: {} | ffprobe: {}", ffmpeg or "không tìm thấy", ffprobe or "không tìm thấy")
    except Exception:  # noqa: BLE001
        logger.exception("FFmpeg lookup failed")

    from app.services.activity_log_service import log_activity

    log_activity("system", "Khởi động ứng dụng", f"v{settings.app_version}")

    from app.ui.windows.main_window import MainWindow

    try:
        window = MainWindow()
    except Exception as e:  # noqa: BLE001
        logger.exception("Main window failed to initialize")
        _fatal("Không thể khởi tạo giao diện", f"{e}\n\nXem log tại: {settings.effective_log_dir}")
        close_database()
        return 1

    window.show()
    logger.info("Application ready — showing main window")

    exit_code = app.exec()

    logger.info("Application shutting down...")
    log_activity("system", "Tắt ứng dụng")
    close_database()
    logger.info("Shutdown complete. Exit code: {}", exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
