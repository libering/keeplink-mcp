# Feature: keeplink-mcp
"""Unit tests for logging setup with RotatingFileHandler.

Validates: Requirements 6.1, 6.2, 6.3
"""

from __future__ import annotations

import logging
import logging.handlers
import tempfile
from pathlib import Path

from keeplink_mcp.logging_setup import setup_logging


class TestLogRotationSetup:
    """Unit tests for log rotation configuration.

    **Validates: Requirements 6.1, 6.2, 6.3**
    """

    def test_rotating_file_handler_used_when_log_file_configured(self) -> None:
        """When log_file is provided, RotatingFileHandler shall be used.

        **Validates: Requirements 6.1**
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "test.log"
            logger = setup_logging(level="INFO", log_file=log_path)

            # Find file handler among logger handlers
            file_handlers = [
                h
                for h in logger.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            ]

            assert len(file_handlers) == 1, (
                f"Expected exactly 1 RotatingFileHandler, found {len(file_handlers)}"
            )

            # Cleanup
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)

    def test_max_bytes_passed_correctly_to_handler(self) -> None:
        """RotatingFileHandler shall have correct maxBytes value.

        **Validates: Requirements 6.2**
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "test.log"
            custom_max_bytes = 5 * 1024 * 1024  # 5 MB

            logger = setup_logging(
                level="INFO",
                log_file=log_path,
                max_bytes=custom_max_bytes,
            )

            file_handler = next(
                h
                for h in logger.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            )

            assert file_handler.maxBytes == custom_max_bytes, (
                f"Expected maxBytes={custom_max_bytes}, got {file_handler.maxBytes}"
            )

            # Cleanup
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)

    def test_backup_count_passed_correctly_to_handler(self) -> None:
        """RotatingFileHandler shall have correct backupCount value.

        **Validates: Requirements 6.3**
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "test.log"
            custom_backup_count = 3

            logger = setup_logging(
                level="INFO",
                log_file=log_path,
                backup_count=custom_backup_count,
            )

            file_handler = next(
                h
                for h in logger.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            )

            assert file_handler.backupCount == custom_backup_count, (
                f"Expected backupCount={custom_backup_count}, "
                f"got {file_handler.backupCount}"
            )

            # Cleanup
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)

    def test_default_max_bytes_is_10mb(self) -> None:
        """Default maxBytes shall be 10 MB.

        **Validates: Requirements 6.2**
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "test.log"

            logger = setup_logging(level="INFO", log_file=log_path)

            file_handler = next(
                h
                for h in logger.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            )

            expected_default = 10 * 1024 * 1024  # 10 MB
            assert file_handler.maxBytes == expected_default, (
                f"Expected default maxBytes={expected_default}, "
                f"got {file_handler.maxBytes}"
            )

            # Cleanup
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)

    def test_default_backup_count_is_5(self) -> None:
        """Default backupCount shall be 5.

        **Validates: Requirements 6.3**
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "test.log"

            logger = setup_logging(level="INFO", log_file=log_path)

            file_handler = next(
                h
                for h in logger.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            )

            assert file_handler.backupCount == 5, (
                f"Expected default backupCount=5, got {file_handler.backupCount}"
            )

            # Cleanup
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)

    def test_no_file_handler_when_log_file_is_none(self) -> None:
        """When log_file is None, no RotatingFileHandler shall be added.

        This verifies the conditional file handler behavior.
        """
        logger = setup_logging(level="INFO", log_file=None)

        file_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]

        assert len(file_handlers) == 0, (
            f"Expected no RotatingFileHandler when log_file=None, "
            f"found {len(file_handlers)}"
        )

        # Cleanup
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)

    def test_console_handler_always_present(self) -> None:
        """Console handler shall always be added regardless of log_file.

        This verifies that console output is never disabled.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "test.log"

            # Test with log_file
            logger = setup_logging(level="INFO", log_file=log_path)

            console_handlers = [
                h for h in logger.handlers if isinstance(h, logging.StreamHandler)
                and not isinstance(h, logging.handlers.RotatingFileHandler)
            ]

            assert len(console_handlers) >= 1, (
                "Expected at least one console handler"
            )

            # Cleanup
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)

        # Test without log_file
        logger = setup_logging(level="INFO", log_file=None)

        console_handlers = [
            h for h in logger.handlers if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.handlers.RotatingFileHandler)
        ]

        assert len(console_handlers) >= 1, (
            "Expected at least one console handler"
        )

        # Cleanup
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)

    def test_log_file_directory_created_if_not_exists(self) -> None:
        """Parent directory for log file shall be created if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a path with non-existent parent directory
            log_path = Path(tmpdir) / "subdir" / "nested" / "test.log"

            # Directory should not exist yet
            assert not log_path.parent.exists()

            logger = setup_logging(level="INFO", log_file=log_path)

            # Directory should now exist
            assert log_path.parent.exists(), (
                f"Expected directory {log_path.parent} to be created"
            )

            # Cleanup
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)
