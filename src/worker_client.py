"""Worker registration and capacity tracking for Command Center integration.

Enables crew-runner to register with Command Center and report real-time capacity.
Maintains active session count and sends periodic heartbeats.

Usage:
    from worker_client import init_worker_client, increment_sessions, decrement_sessions
    
    # On startup
    worker_client = init_worker_client(
        command_center_url="http://command-center:3007",
        worker_id="crew-runner-1",
        api_key="runner_xxx",
        max_sessions=5
    )
    
    # On task start
    increment_sessions()
    
    # On task complete
    decrement_sessions()
"""
import asyncio
import logging
import os
import threading
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Global state
_worker_client: Optional["WorkerClient"] = None
_active_sessions = 0
_max_sessions = 1
_lock = threading.Lock()


class WorkerClient:
    """Handles worker registration and heartbeat with Command Center."""

    def __init__(
        self,
        command_center_url: str,
        worker_id: str,
        api_key: str,
        max_sessions: int = 1,
        heartbeat_interval: int = 30,
    ):
        self.command_center_url = command_center_url.rstrip("/")
        self.worker_id = worker_id
        self.api_key = api_key
        self.max_sessions = max_sessions
        self.heartbeat_interval = heartbeat_interval
        self._stop_event = threading.Event()
        self._heartbeat_thread: Optional[threading.Thread] = None

    def start(self):
        """Start the heartbeat loop in a background thread."""
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            logger.warning("Heartbeat thread already running")
            return

        self._stop_event.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name="worker-heartbeat"
        )
        self._heartbeat_thread.start()
        logger.info("Worker heartbeat started (interval: %ds)", self.heartbeat_interval)

    def stop(self):
        """Stop the heartbeat loop."""
        if not self._heartbeat_thread or not self._heartbeat_thread.is_alive():
            return

        self._stop_event.set()
        self._heartbeat_thread.join(timeout=5)
        logger.info("Worker heartbeat stopped")

    def _heartbeat_loop(self):
        """Background thread that sends periodic heartbeats."""
        # Send initial heartbeat immediately
        self._send_heartbeat()

        # Then send every interval
        while not self._stop_event.is_set():
            self._stop_event.wait(self.heartbeat_interval)
            if not self._stop_event.is_set():
                self._send_heartbeat()

    def _send_heartbeat(self):
        """Send a single heartbeat to Command Center."""
        global _active_sessions

        with _lock:
            active = _active_sessions

        try:
            url = f"{self.command_center_url}/api/workers/{self.worker_id}/heartbeat"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            data = {
                "active_sessions": active,
                "max_sessions": self.max_sessions,
                "status": "busy" if active >= self.max_sessions else "online"
            }

            with httpx.Client(timeout=5.0) as client:
                response = client.post(url, json=data, headers=headers)
                response.raise_for_status()

            logger.debug(
                "Heartbeat sent: %s (active=%d/%d)",
                self.worker_id, active, self.max_sessions
            )

        except httpx.TimeoutException:
            logger.error("Heartbeat error: timed out")
        except httpx.HTTPStatusError as e:
            logger.error("Heartbeat error: HTTP %d - %s", e.response.status_code, e.response.text)
        except Exception as e:
            logger.error("Heartbeat error: %s", e)


def init_worker_client(
    command_center_url: Optional[str] = None,
    worker_id: Optional[str] = None,
    api_key: Optional[str] = None,
    max_sessions: Optional[int] = None,
) -> Optional[WorkerClient]:
    """Initialize the worker client and start heartbeat loop.
    
    Args:
        command_center_url: Command Center API URL (default: COMMAND_CENTER_API_URL env var)
        worker_id: Worker identifier (default: WORKER_ID env var)
        api_key: Runner API key (default: RUNNER_API_KEY env var)
        max_sessions: Maximum concurrent sessions (default: CREW_RUNNER_MAX_SESSIONS env var or 1)
    
    Returns:
        WorkerClient instance if all parameters are provided, None otherwise
    """
    global _worker_client, _max_sessions

    # Read from environment if not provided
    command_center_url = command_center_url or os.getenv("COMMAND_CENTER_API_URL")
    worker_id = worker_id or os.getenv("WORKER_ID")
    api_key = api_key or os.getenv("RUNNER_API_KEY")
    max_sessions_env = os.getenv("CREW_RUNNER_MAX_SESSIONS")
    
    if max_sessions is None:
        max_sessions = int(max_sessions_env) if max_sessions_env else 1

    # Skip registration if any required parameter is missing
    if not all([command_center_url, worker_id, api_key]):
        logger.info(
            "Worker registration skipped (missing COMMAND_CENTER_API_URL, WORKER_ID, or RUNNER_API_KEY)"
        )
        return None

    _max_sessions = max_sessions

    _worker_client = WorkerClient(
        command_center_url=command_center_url,
        worker_id=worker_id,
        api_key=api_key,
        max_sessions=max_sessions,
    )

    _worker_client.start()
    logger.info("Worker client initialized: %s @ %s", worker_id, command_center_url)

    return _worker_client


def increment_sessions():
    """Increment active session count (call on task start)."""
    global _active_sessions
    with _lock:
        _active_sessions += 1
        logger.debug("Active sessions: %d/%d", _active_sessions, _max_sessions)


def decrement_sessions():
    """Decrement active session count (call on task complete)."""
    global _active_sessions
    with _lock:
        if _active_sessions > 0:
            _active_sessions -= 1
        logger.debug("Active sessions: %d/%d", _active_sessions, _max_sessions)


def get_active_sessions() -> int:
    """Get current active session count."""
    with _lock:
        return _active_sessions


def get_max_sessions() -> int:
    """Get maximum session capacity."""
    return _max_sessions
