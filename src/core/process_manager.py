"""
process_manager.py — Process lifecycle and background task manager for scrapers.

Ensures that child processes (ATS scraper, Google scraper, AI ranker) are tracked,
can be stopped cleanly from the GUI, and are reliably terminated when the GUI exits
or when Ctrl+C (SIGINT) / SIGTERM is received.
Also cleans up any dangling/orphaned scraper processes on application startup.
"""
import os
import sys
import signal
import atexit
import subprocess
from typing import Set

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

_REGISTERED_PROCESSES: Set[subprocess.Popen] = set()
_CLEANUP_CALLED = False


def register_child_process(proc: subprocess.Popen):
    """Register a subprocess for lifecycle tracking."""
    if proc:
        _REGISTERED_PROCESSES.add(proc)


def unregister_child_process(proc: subprocess.Popen):
    """Unregister a subprocess when it terminates normally."""
    _REGISTERED_PROCESSES.discard(proc)


def get_active_child_processes():
    """Return a list of currently registered active child processes."""
    return [p for p in _REGISTERED_PROCESSES if p.poll() is None]


def create_stop_flag(flag_path="stop.flag"):
    """Write stop flag to signal all active scrapers/workers to stop."""
    try:
        with open(flag_path, "w") as f:
            f.write("stop")
    except Exception as e:
        print(f"Warning: Could not create stop flag: {e}")


def clear_stop_flag(flag_path="stop.flag"):
    """Remove stop flag if it exists."""
    if os.path.exists(flag_path):
        try:
            os.remove(flag_path)
        except Exception:
            pass


def cleanup_all_child_processes():
    """Terminate and kill all registered child processes gracefully, then forcefully."""
    global _CLEANUP_CALLED
    if _CLEANUP_CALLED:
        return
    _CLEANUP_CALLED = True

    create_stop_flag()

    procs = [p for p in _REGISTERED_PROCESSES if p.poll() is None]
    if not procs:
        _REGISTERED_PROCESSES.clear()
        return

    # 1. Send SIGTERM first so processes can flush batches and close DB connections
    for p in procs:
        try:
            if p.poll() is None:
                p.terminate()
        except Exception:
            pass

    # 2. Wait up to 1.5 seconds for clean exit
    alive = []
    for p in procs:
        try:
            if p.poll() is None:
                try:
                    p.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    alive.append(p)
        except Exception:
            pass

    # 3. Force kill any remaining processes
    for p in alive:
        try:
            if p.poll() is None:
                p.kill()
        except Exception:
            pass

    _REGISTERED_PROCESSES.clear()


def cleanup_orphaned_scrapers(repo_root=None):
    """
    Find and terminate any orphaned scraper or ranking processes from previous dead sessions.
    This prevents database locks from dangling background processes after terminal Ctrl+C or kill.
    """
    if not HAS_PSUTIL:
        return 0

    if repo_root is None:
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    current_pid = os.getpid()
    target_scripts = (
        "ats_scraper.py",
        "intern_scraper.py",
        "intern_scraper_fast.py",
        "intern_scraper_overnight.py",
        "rank_internships.py",
    )

    cleaned_count = 0
    for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'ppid']):
        try:
            if proc.pid == current_pid:
                continue

            cmdline = proc.info.get('cmdline') or []
            cmd_str = " ".join(cmdline)

            # Check if this process is executing one of our scraper scripts
            is_scraper = any(script in cmd_str for script in target_scripts)
            in_our_repo = (repo_root in cmd_str) or ("scraper_env" in cmd_str) or ("web-scraper-job-search" in cmd_str)

            if is_scraper and in_our_repo:
                print(f"🧹 [Auto-Cleanup] Found orphaned scraper process (PID {proc.pid}: {cmd_str[:60]}...). Terminating to free DB lock...")
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except psutil.TimeoutExpired:
                    proc.kill()
                cleaned_count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    if cleaned_count > 0:
        print(f"✅ Cleaned up {cleaned_count} orphaned scraper process(es). Database is unlocked.")

    return cleaned_count


def setup_process_signals():
    """Register exit and signal handlers to ensure clean child termination."""
    atexit.register(cleanup_all_child_processes)

    def _sig_handler(signum, frame):
        cleanup_all_child_processes()
        sys.exit(0)

    try:
        signal.signal(signal.SIGINT, _sig_handler)
    except (ValueError, AttributeError):
        pass
    try:
        signal.signal(signal.SIGTERM, _sig_handler)
    except (ValueError, AttributeError):
        pass

