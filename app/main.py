"""LiveFlow AI Studio — Application entry point."""

from __future__ import annotations

import sys

from loguru import logger


def main() -> int:
    """Main entry point for the application."""

    # Import PySide6 early to check availability
    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import Qt
    except ImportError:
        print("❌ PySide6 chưa được cài đặt!")
        print("   Chạy: pip install PySide6")
        return 1

    # Load config
    from app.config.settings import get_settings

    settings = get_settings()

    # Setup logging
    from app.core.logging import setup_logging

    setup_logging(
        log_dir=settings.effective_log_dir,
        log_level=settings.log_level,
        debug=settings.debug,
    )

    logger.info("=" * 60)
    logger.info("LiveFlow AI Studio v{} starting...", settings.app_version)
    logger.info("=" * 60)

    # Bootstrap — check dependencies and create directories
    from app.bootstrap import (
        check_critical_failures,
        initialize_app_directories,
        run_all_checks,
    )

    initialize_app_directories(settings.data_dir)

    logger.info("Checking dependencies...")
    dep_results = run_all_checks()
    critical_failures = check_critical_failures(dep_results)

    if critical_failures:
        for failure in critical_failures:
            logger.error("Critical dependency missing: {}", failure.name)
        print("\n❌ Thiếu dependency bắt buộc:")
        for f in critical_failures:
            print(f"   {f.status_text}")
        return 1

    # Initialize database
    from app.database.session import init_database

    logger.info("Initializing database...")
    init_database(settings.db_url, echo=settings.debug)

    # Enable high DPI scaling (must be set before QApplication creation)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    # Create Qt Application
    app = QApplication(sys.argv)
    app.setApplicationName(settings.app_name)
    app.setApplicationVersion(settings.app_version)
    app.setOrganizationName("LiveFlow")

    # Create and show main window
    from app.ui.windows.main_window import MainWindow

    window = MainWindow()
    window.show()

    logger.info("Application ready — showing main window")

    # Run event loop
    exit_code = app.exec()

    # Cleanup
    logger.info("Application shutting down...")
    from app.database.session import close_database

    close_database()
    logger.info("Shutdown complete. Exit code: {}", exit_code)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
