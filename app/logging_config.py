"""Structured logging configuration."""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Any


class JSONFormatter(logging.Formatter):
    """JSON log formatter for structured logging."""
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        # Add extra fields
        extra = {}
        for key, value in record.__dict__.items():
            if key not in [
                "name", "msg", "args", "created", "filename", "funcName", 
                "levelname", "levelno", "lineno", "module", "msecs", 
                "message", "pathname", "process", "processName", "relativeCreated",
                "thread", "threadName", "exc_info", "exc_text", "stack_info"
            ]:
                extra[key] = value
        
        if extra:
            log_data["extra"] = extra
        
        return json.dumps(log_data)


class ConsoleFormatter(logging.Formatter):
    """Colored console formatter for development."""
    
    # ANSI color codes
    COLORS = {
        "DEBUG": "\033[36m",      # Cyan
        "INFO": "\033[32m",       # Green
        "WARNING": "\033[33m",    # Yellow
        "ERROR": "\033[31m",      # Red
        "CRITICAL": "\033[35m",   # Magenta
    }
    RESET = "\033[0m"
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record with colors."""
        timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        level_color = self.COLORS.get(record.levelname, "")
        level = f"{level_color}{record.levelname:8}{self.RESET}"
        logger_name = record.name
        message = record.getMessage()
        
        log_line = f"[{timestamp}] [{level}] [{logger_name}] {message}"
        
        # Add exception if present
        if record.exc_info:
            log_line += "\n" + self.formatException(record.exc_info)
        
        return log_line


def setup_logging(
    log_level: str = "INFO",
    log_format: str = "console",
    log_dir: str = "./logs"
) -> None:
    """
    Configure application logging.
    
    Args:
        log_level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_format: Format type ("console" or "json")
        log_dir: Directory for log files
    """
    # Create log directory
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    
    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # Capture everything, handlers will filter
    
    # Clear existing handlers
    root_logger.handlers.clear()
    
    # Choose formatter
    if log_format == "json":
        formatter = JSONFormatter()
    else:
        formatter = ConsoleFormatter()
    
    # Console handler (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, log_level.upper()))
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # File handler - all logs (rotating)
    file_handler = RotatingFileHandler(
        log_path / "server.log",
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
    )
    file_handler.setLevel(getattr(logging, log_level.upper()))
    file_handler.setFormatter(JSONFormatter())  # Always JSON for files
    root_logger.addHandler(file_handler)
    
    # File handler - errors only (rotating)
    error_handler = RotatingFileHandler(
        log_path / "error.log",
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(JSONFormatter())
    root_logger.addHandler(error_handler)
    
    # Set log levels by module
    logging.getLogger("app").setLevel(logging.INFO)
    logging.getLogger("app.orchestrator").setLevel(logging.INFO)
    logging.getLogger("app.backup").setLevel(logging.INFO)
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    
    # Log startup message
    logger = logging.getLogger("app.logging")
    logger.info(
        f"Logging configured: level={log_level}, format={log_format}, dir={log_dir}"
    )
