import json
import multiprocessing
import os
import queue  # Use standard queue exception
import webbrowser
from pathlib import Path  # Use pathlib for KOLIBRI_HOME

import wx

from kolibri_app.constants import APP_NAME
from kolibri_app.logger import logging
from kolibri_app.view import KolibriView
from kolibri_app.view import LOADER_URL

# Use multiprocessing for the server process


STATE_FILE = "app_state.json"

# State keys
URL = "URL"

# Server monitoring constants
SERVER_STARTUP_CHECK_INTERVAL = 100  # milliseconds
SERVER_RUNNING_CHECK_INTERVAL = 1000  # milliseconds
SERVER_SHUTDOWN_TIMEOUT = 5  # seconds


# Helper to get KOLIBRI_HOME without importing Kolibri
def get_kolibri_home():
    """Get the Kolibri home directory without importing Kolibri"""
    return os.environ.get("KOLIBRI_HOME", str(Path.home() / ".kolibri"))


class KolibriApp(wx.App):
    def OnInit(self):
        """
        Start your UI and app run loop here.
        """

        self.SetAppName(APP_NAME)

        instance_name = "{}_{}".format(APP_NAME, wx.GetUserId())
        self._checker = wx.SingleInstanceChecker(instance_name)
        if self._checker.IsAnotherRunning():
            logging.warning("Another instance is already running.")
            return False

        self.windows = []
        self.kolibri_server_process = None  # Stores the multiprocessing.Process object
        self.server_ready_queue = multiprocessing.Queue()  # For server ready signal
        self.kolibri_origin = None  # Will be set like 'http://localhost:port'
        self.server_monitor_timer = None  # wx.Timer for monitoring

        # Create the initial window (will show loading screen)
        # Pass LOADER_URL explicitly if server isn't ready (which it isn't yet)
        self.create_kolibri_window(url=LOADER_URL)
        self.start_server()

        return True

    @property
    def view(self):
        if self.windows:
            return self.windows[0]
        return None

    def start_server(self):
        """Start the Kolibri server using multiprocessing.Process."""
        # Check if already running
        if self.kolibri_server_process and self.kolibri_server_process.is_alive():
            logging.info("Server process already running")
            return

        logging.info("Starting Kolibri server via multiprocessing.Process")

        try:
            from kolibri_app._server_process import main as server_main_func

            self.kolibri_server_process = multiprocessing.Process(
                target=server_main_func,
                args=(self.server_ready_queue,),
                daemon=True,  # Ensure process exits if main app exits unexpectedly
            )
            self.kolibri_server_process.start()

            logging.info(
                f"Kolibri server process started with PID {self.kolibri_server_process.pid}"
            )
            self.start_server_monitoring()

        except Exception as e:
            logging.error(f"Failed to start Kolibri server process: {e}", exc_info=True)
            # Use CallAfter for GUI safety from non-main thread/context
            wx.CallAfter(
                wx.MessageBox,
                f"Failed to start Kolibri server: {str(e)}\n\nCheck logs for details.",
                "Server Startup Error",
                wx.OK | wx.ICON_ERROR,
            )

    # Removed start_kolibri_server method, its logic is now in _server_process.py

    def start_server_monitoring(self):
        """Start monitoring the server process status and ready queue."""
        if self.server_monitor_timer:
            self.server_monitor_timer.Stop()
        logging.info(
            "Starting server monitoring timer (interval: {}ms)".format(
                SERVER_STARTUP_CHECK_INTERVAL
            )
        )
        self.server_monitor_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.check_server_status, self.server_monitor_timer)
        # Start with faster interval, slow down once server is ready
        self.server_monitor_timer.Start(SERVER_STARTUP_CHECK_INTERVAL)

    def check_server_status(self, event=None):
        """Periodically check server process and ready queue (replaces AppPlugin)."""

        # 1. Check the queue for the READY signal
        if self.kolibri_origin is None:
            try:
                message = self.server_ready_queue.get(block=False)  # Non-blocking check
                if (
                    message
                    and isinstance(message, str)
                    and message.startswith("READY:")
                ):
                    try:
                        port = int(message.split(":")[1].strip())
                        logging.info(
                            f"Server ready signal received via queue on port {port}"
                        )
                        wx.CallAfter(self.load_kolibri, port)
                        # Slow down monitoring now that server is up
                        if self.server_monitor_timer:
                            self.server_monitor_timer.Stop()
                            self.server_monitor_timer.Start(
                                SERVER_RUNNING_CHECK_INTERVAL
                            )
                            logging.info(
                                "Server ready, slowing monitoring interval to {}ms".format(
                                    SERVER_RUNNING_CHECK_INTERVAL
                                )
                            )
                    except (ValueError, IndexError, TypeError) as e:
                        logging.error(
                            f"Failed to parse server ready message '{message}': {e}"
                        )
                else:
                    logging.warning(
                        f"Received unexpected message on ready queue: {message}"
                    )
            except queue.Empty:
                # Queue is empty, server not ready yet, continue checking
                pass
            except Exception as e:
                # Handle other potential queue errors
                logging.error(f"Error checking server ready queue: {e}")

        # 2. Check if the server process is still alive
        if self.kolibri_server_process:
            if not self.kolibri_server_process.is_alive():
                exitcode = self.kolibri_server_process.exitcode
                logging.error(
                    f"Server process PID {self.kolibri_server_process.pid} exited unexpectedly with code {exitcode}."
                )

                # Stop monitoring
                if self.server_monitor_timer:
                    self.server_monitor_timer.Stop()
                    self.server_monitor_timer = None

                server_pid = self.kolibri_server_process.pid  # Store pid for logging

                # Show error message ONLY if the server failed *before* signaling readiness
                if self.kolibri_origin is None:
                    logging.error(
                        f"Server process (PID {server_pid}) failed before signaling readiness."
                    )
                    wx.CallAfter(
                        wx.MessageBox,
                        f"The Kolibri server process failed to start (code {exitcode}).\n\n"
                        f"Please check the logs for details:\n"
                        f"{os.path.join(get_kolibri_home(), 'logs')}",
                        "Server Startup Error",
                        wx.OK | wx.ICON_ERROR,
                    )
                else:
                    logging.warning(
                        f"Server process (PID {server_pid}) exited after becoming ready."
                    )

                # Clean up process reference now
                self.kolibri_server_process = None
                self.kolibri_origin = None  # Reset origin if server died

        # If timer exists but process is gone (e.g., after shutdown), stop timer
        elif self.server_monitor_timer:
            logging.info("Server process is gone, stopping monitor.")
            self.server_monitor_timer.Stop()
            self.server_monitor_timer = None

    def create_kolibri_window(self, url=None):
        """Creates a new Kolibri window. Handles initial loading state."""
        # Determine initial URL based on whether server is ready
        initial_url = LOADER_URL
        if self.kolibri_origin:
            # Server is ready, use provided url or the origin root
            initial_url = url or self.kolibri_origin + "/"
            logging.info(
                f"Creating Kolibri window, server ready, initial URL: {initial_url}"
            )
        elif url and url != LOADER_URL:
            # A specific URL was requested *before* server ready (e.g. reopening app)
            # We still show loader first, but might want to store 'url' to load later.
            logging.info(
                f"Creating Kolibri window, server not ready, requested URL '{url}' ignored, using loader."
            )
        else:
            # Server not ready, no specific URL requested, use loading page
            logging.info(
                f"Creating Kolibri window, server not ready, initial URL: {initial_url}"
            )

        window = KolibriView(self, url=initial_url)
        self.windows.append(window)
        window.show()
        return window

    def should_load_url(self, url):
        """Determine if the WebView should load a URL or open externally."""
        if url is None:
            return False

        is_loading = url.startswith("loading:")
        # Check for localhost more robustly
        is_localhost = url.startswith("http://localhost:") or url.startswith(
            "http://127.0.0.1:"
        )
        # Basic check for relative paths (can be improved if needed)
        is_relative = (
            not url.startswith("http")
            and not url.startswith(":")
            and not url.startswith("loading:")
        )

        # If server is ready, allow navigation within its origin or to other localhost ports
        if self.kolibri_origin:
            if url.startswith(self.kolibri_origin) or is_relative:
                logging.debug(f"Allowing navigation to Kolibri URL: {url}")
                return True
            # Allow other localhost
            elif is_localhost:
                logging.debug(f"Allowing navigation to other localhost URL: {url}")
                return True
        # Always allow the loading screen URL itself
        elif is_loading:
            logging.debug(f"Allowing navigation to loading screen: {url}")
            return True

        # If it's an external http/https URL, open in browser and block in WebView
        if url.startswith("http"):
            logging.info(f"Opening external URL in browser: {url}")
            wx.CallAfter(webbrowser.open, url)
            return False

        # Block other schemes (e.g., file://) or disallowed URLs
        logging.warning(f"Blocking navigation to: {url}")
        return False

    def get_state(self):
        """Load app state from JSON file (kept from updated - more robust)."""
        state_file = os.path.join(get_kolibri_home(), STATE_FILE)
        try:
            if os.path.exists(state_file):
                with open(state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except (IOError, PermissionError, json.JSONDecodeError, ValueError) as e:
            logging.warning(f"Failed to read state file {state_file}: {e}")
        return {}

    def save_state(self, view=None):
        """Save app state to JSON file (kept from updated - conditional save)."""
        # Use the currently active view if none is passed (or first window)
        view_to_save = view or self.view
        state = {}
        # Only save state if the server was actually ready and we have a view
        if view_to_save and self.kolibri_origin:
            current_url = view_to_save.get_url()
            if current_url and current_url.startswith(self.kolibri_origin):
                state[URL] = current_url
                logging.info(f"Saving state: URL={current_url}")
            else:
                logging.info(
                    f"Not saving URL ({current_url}), not a valid Kolibri origin URL."
                )
        else:
            logging.info("Not saving state (server not ready or no view).")

        # Write the state (even if empty)
        state_file = os.path.join(get_kolibri_home(), STATE_FILE)
        try:
            os.makedirs(os.path.dirname(state_file), exist_ok=True)
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except (IOError, PermissionError, TypeError) as e:
            logging.error(f"Failed to save state file {state_file}: {e}")

    def load_kolibri(self, listen_port):
        """Called when the server is ready (via timer checking queue)."""
        # Prevent duplicate loading if signal somehow repeats
        current_origin = f"http://localhost:{listen_port}"
        if self.kolibri_origin == current_origin:
            logging.warning(
                f"load_kolibri called again for the same origin {self.kolibri_origin}. Ignoring."
            )
            return

        self.kolibri_origin = current_origin
        logging.info(f"Kolibri server confirmed ready on {self.kolibri_origin}")

        # Check for saved URL from state
        saved_state = self.get_state()
        logging.debug(f"Persisted State: {saved_state}")

        # Default to Kolibri root URL
        root_url = (
            self.kolibri_origin + "/"
        )  # Original used interface.get_initialize_url

        # Use saved URL only if it's valid and from the *current* origin
        if (
            URL in saved_state
            and isinstance(saved_state[URL], str)
            and saved_state[URL].startswith(self.kolibri_origin)
        ):
            # Basic validation: ensure it's not just the origin root path itself
            if (
                saved_state[URL] != self.kolibri_origin
                and saved_state[URL] != self.kolibri_origin + "/"
            ):
                root_url = saved_state[URL]
                logging.info(f"Using saved URL from state: {root_url}")
            else:
                logging.info("Saved URL is root, using default root.")
        else:
            logging.info(
                "No valid saved URL found for this origin, using default root."
            )

        # Load the URL in all windows currently showing the loader
        if not self.windows:
            logging.error("Server ready, but no windows available to load Kolibri URL!")
            return

        logging.info(
            f"Attempting to load Kolibri URL ({root_url}) in relevant windows..."
        )
        for window in self.windows:
            try:
                # Check window validity (might have been closed)
                if not window or not window.view:
                    logging.debug("Skipping update for a closed or invalid window.")
                    continue

                current_win_url = window.get_url()
                # Load if the window is showing the loader OR if it hasn't loaded the origin yet
                if current_win_url == LOADER_URL or not (
                    current_win_url and current_win_url.startswith(self.kolibri_origin)
                ):
                    logging.info(
                        f"Loading Kolibri URL in window (current: {current_win_url or 'None'})"
                    )
                    wx.CallAfter(window.load_url, root_url)
                else:
                    logging.debug(
                        f"Window already showing Kolibri content ({current_win_url}), skipping update."
                    )
            except Exception as e:
                # Catch errors if window becomes invalid between check and access
                logging.error(f"Error trying to update window URL: {e}", exc_info=True)

    def shutdown(self):
        """Clean shutdown of the app, including the server process (from updated)."""
        logging.info("Initiating Kolibri app shutdown...")

        # Stop the monitor timer first
        if self.server_monitor_timer:
            logging.debug("Stopping server monitor timer.")
            self.server_monitor_timer.Stop()
            self.server_monitor_timer = None

        # Attempt to terminate the server process gracefully
        if self.kolibri_server_process and self.kolibri_server_process.is_alive():
            pid = self.kolibri_server_process.pid
            logging.info(
                f"Attempting to terminate Kolibri server process (PID {pid})..."
            )
            try:
                self.kolibri_server_process.terminate()
                self.kolibri_server_process.join(timeout=SERVER_SHUTDOWN_TIMEOUT)

                if self.kolibri_server_process.is_alive():
                    # Process didn't exit after SIGTERM, force kill
                    logging.warning(
                        f"Server process (PID {pid}) did not terminate gracefully after {SERVER_SHUTDOWN_TIMEOUT}s, killing..."
                    )
                    self.kolibri_server_process.kill()  # Sends SIGKILL/TerminateProcess
                    self.kolibri_server_process.join(timeout=2)
                    if self.kolibri_server_process.is_alive():
                        logging.error(f"Failed to kill server process (PID {pid}).")
                    else:
                        logging.info(f"Kolibri server process (PID {pid}) killed.")
                else:
                    logging.info(
                        f"Kolibri server process (PID {pid}) terminated gracefully."
                    )

            except Exception as e:
                logging.error(
                    f"Error during server process shutdown (PID {pid}): {e}",
                    exc_info=True,
                )
            finally:
                # Ensure process object resources are released if possible
                if hasattr(self.kolibri_server_process, "close") and callable(
                    self.kolibri_server_process.close
                ):
                    try:
                        self.kolibri_server_process.close()
                        logging.debug(f"Closed handles for server process (PID {pid}).")
                    except Exception as close_e:
                        # Buffer exceptions not alwaysRaisable on Windows after terminate/kill
                        logging.warning(
                            f"Error closing server process {pid} (may be expected): {close_e}"
                        )
                self.kolibri_server_process = None
        elif self.kolibri_server_process:
            # Process object exists but is not alive (already exited)
            pid = self.kolibri_server_process.pid
            logging.info(
                f"Server process (PID {pid}) already exited before shutdown sequence."
            )
            if hasattr(self.kolibri_server_process, "close") and callable(
                self.kolibri_server_process.close
            ):
                try:
                    self.kolibri_server_process.close()
                except Exception as close_e:
                    logging.warning(
                        f"Error closing already exited server process {pid}: {close_e}"
                    )
            self.kolibri_server_process = None
        else:
            logging.info(
                "No Kolibri server process was running or reference already cleared."
            )

        logging.info("Kolibri app shutdown sequence complete.")

    def OnExit(self):
        """Called when the application is exiting (standard wxPython)."""
        logging.debug("wx.App OnExit called.")
        # Ensure shutdown logic runs if not already triggered by window close etc.
        self.shutdown()
        return 0

    def MacReopenApp(self):
        """Called on macOS when the dock icon is clicked"""
        logging.debug("MacReopenApp event received.")
        # If no windows are open, create a new one.
        if not self.windows:
            # Create window - it will show loader or Kolibri depending on server state
            self.create_kolibri_window(url=self.kolibri_origin or LOADER_URL)
        # If windows exist, bring the main/first one to the front.
        elif self.view:
            # Ensure the underlying frame exists before trying to Raise
            if self.view.view:
                self.view.view.Raise()
            else:
                logging.warning(
                    "MacReopenApp: Main view's underlying frame (view.view) does not exist."
                )
        else:
            logging.warning("MacReopenApp: No main view (self.view) found to raise.")
