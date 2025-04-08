import os
import sys
import signal
import logging
from multiprocessing import freeze_support, Queue


# --- Logging Setup ---
try:
    # Attempt to use Kolibri's logging utilities
    from kolibri.utils.conf import LOG_ROOT, KOLIBRI_HOME
    from kolibri.utils.logger import KolibriTimedRotatingFileHandler
    # Ensure logs directory exists within KOLIBRI_HOME
    os.makedirs(os.path.join(KOLIBRI_HOME, 'logs'), exist_ok=True)
except ImportError as e:
     # Fallback logging if Kolibri utils fail (e.g., during initial setup/testing)
     print(f"ERROR: Failed to import Kolibri logging utils: {e}", file=sys.stderr, flush=True)
     # Define a default LOG_ROOT if Kolibri's couldn't be imported
     LOG_ROOT = os.path.join(os.path.expanduser("~"), ".kolibri", "logs")
     os.makedirs(LOG_ROOT, exist_ok=True)
     # Configure basic logging to stderr as a fallback
     logging.basicConfig(stream=sys.stderr, format="%(levelname)s: %(message)s", level=logging.INFO)
     logger = logging.getLogger("kolibri_server_process_fallback")
     logger.error("Using fallback logging due to import error.")

# Configure dedicated server process logging
logger = logging.getLogger("kolibri_server_process")

# Prevent adding handlers multiple times if basicConfig was called in fallback
if not logger.hasHandlers():
     logger.setLevel(logging.INFO)

log_basename = "kolibri-server.txt"
log_filename = os.path.join(LOG_ROOT, log_basename)
try:
    file_handler = KolibriTimedRotatingFileHandler(
        filename=log_filename, encoding="utf-8", when="midnight", backupCount=30
    )
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.info(f"Logging initialized successfully to {log_filename}")
except NameError: # If KolibriTimedRotatingFileHandler failed to import
     logger.error(f"Failed to create file handler for {log_filename} (KolibriTimedRotatingFileHandler not found). Logging to stderr only.")
except Exception as log_e:
     # Catch any other errors during file handler setup
     logger.error(f"Error setting up file logging for {log_filename}: {log_e}")


# --- Main Server Logic ---
def main(ready_queue=None):
    """Main entry point for the Kolibri server process"""
    logger.info("--- Kolibri Server Process Started ---")
    logger.info(f"PID: {os.getpid()}")
    logger.info(f"Python Executable: {sys.executable}")
    logger.info(f"sys.argv: {sys.argv}")
    logger.info(f"KOLIBRI_HOME: {os.environ.get('KOLIBRI_HOME', 'Not Set')}")
    logger.info(f"DJANGO_SETTINGS_MODULE: {os.environ.get('DJANGO_SETTINGS_MODULE', 'Not Set')}")
    logger.info(f"Received ready_queue: {'Yes' if ready_queue else 'No'}")

    # --- Graceful shutdown signal handling ---
    def signal_handler(sig, frame):
        """Attempts to gracefully stop the KolibriProcessBus."""
        logger.info(f"Received signal {sig}, initiating shutdown...")
        kolibri_server_instance = None
        # Find the KolibriProcessBus instance in the current frame's local variables.
        # NOTE: This relies on the variable holding the bus being named 'kolibri_server'
        #       within the scope where run() is called. It's a bit fragile but avoids
        #       making kolibri_server global or needing complex callback setups.
        for obj in frame.f_locals.values():
            if hasattr(obj, 'stop') and callable(obj.stop) and type(obj).__name__ == 'KolibriProcessBus':
                kolibri_server_instance = obj
                break

        if kolibri_server_instance:
            logger.info("Attempting to stop KolibriProcessBus...")
            try:
                kolibri_server_instance.stop()
                logger.info("KolibriProcessBus stop signal sent.")
                # The process should exit naturally after the bus stops.
            except Exception as e:
                logger.error(f"Error calling KolibriProcessBus.stop(): {e}", exc_info=True)
                sys.exit(1) # Exit with error if stop fails
        else:
             logger.warning("Could not find KolibriProcessBus instance in local scope to stop gracefully.")
             # Exit directly if bus instance wasn't found to stop.
             sys.exit(0)

    # Register signal handlers for termination (SIGTERM) and interrupt (SIGINT)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # --- Main Kolibri Startup Sequence ---
    try:
        from kolibri.main import initialize, enable_plugin
        from kolibri.utils.server import KolibriProcessBus
        from kolibri.utils.conf import OPTIONS
        # Imports needed for migrations
        from django.core.management import call_command
        from django.db import connections
        from django.db.utils import OperationalError

        logger.info("Kolibri modules imported successfully.")

        # --- Enable necessary plugins ---
        # This MUST happen BEFORE initialize()
        try:
             logger.info("Enabling plugin: kolibri.plugins.app")
             enable_plugin("kolibri.plugins.app")
             logger.info("Enabling plugin: kolibri_app")
             enable_plugin("kolibri_app") # Your custom app plugin
             logger.info("Plugins enabled successfully.")
        except Exception as plugin_e:
             logger.error(f"Failed to enable essential plugins: {plugin_e}", exc_info=True)
             sys.exit(1) # Exit if essential plugins cannot be enabled

        # --- Initialize Kolibri ---
        logger.info("Attempting to call kolibri.main.initialize()...")
        try:
            initialize(skip_update=False)
            logger.info("kolibri.main.initialize() completed successfully.")

            # --- Run Django Migrations ---
            logger.info("Attempting to run Django migrations...")
            try:
                # Ensure the default database connection is closed before migrating
                if 'default' in connections:
                     connections['default'].close()
                     logger.debug("Closed default DB connection before migrating.")

                call_command("migrate", interactive=False)
                logger.info("Django migrations completed successfully.")

            except OperationalError as migrate_db_err:
                 logger.error(f"Database error during migrations: {migrate_db_err}", exc_info=True)
                 sys.exit(1)
            except Exception as migrate_e:
                logger.error(f"Error running Django migrations: {migrate_e}", exc_info=True)
                sys.exit(1)
            # --- End Migrations ---

        except Exception as init_e:
            logger.error(f"kolibri.main.initialize() FAILED: {init_e}", exc_info=True)
            if 'django' in sys.modules:
                try:
                    from django.conf import settings
                    logger.error(f"Django INSTALLED_APPS: {settings.INSTALLED_APPS}")
                except Exception as settings_e:
                    logger.error(f"Could not log Django settings: {settings_e}")
            sys.exit(1) # Exit if initialization fails

        # --- Server Setup ---
        # Determine HTTP and ZIP ports from environment or Kolibri defaults
        http_port_str = os.environ.get("KOLIBRI_HTTP_PORT", str(OPTIONS["Deployment"]["HTTP_PORT"]))
        zip_port_str = os.environ.get("KOLIBRI_ZIP_CONTENT_PORT", str(OPTIONS["Deployment"]["ZIP_CONTENT_PORT"]))

        # Parse HTTP Port
        try:
            http_port = int(http_port_str)
            if http_port == 0:
                 logger.info("HTTP port set to 0, requesting auto-selection by Kolibri.")
        except ValueError:
            logger.warning(f"Invalid KOLIBRI_HTTP_PORT '{http_port_str}', using Kolibri default: {OPTIONS['Deployment']['HTTP_PORT']}")
            http_port = OPTIONS["Deployment"]["HTTP_PORT"]

        # Parse ZIP Port
        try:
            zip_port = int(zip_port_str)
            if zip_port == 0:
                 logger.info("ZIP Content port set to 0, requesting auto-selection by Kolibri.")
        except ValueError:
            logger.warning(f"Invalid KOLIBRI_ZIP_CONTENT_PORT '{zip_port_str}', using Kolibri default: {OPTIONS['Deployment']['ZIP_CONTENT_PORT']}")
            zip_port = OPTIONS["Deployment"]["ZIP_CONTENT_PORT"]

        logger.info(f"Attempting to start Kolibri server on HTTP port: {http_port}, ZIP port: {zip_port}")

        # Create the Kolibri server instance (must be named 'kolibri_server' for signal handler)
        kolibri_server = KolibriProcessBus(
            port=http_port,
            zip_port=zip_port,
        )

        # --- Readiness Signaling ---
        # Define callback function triggered when the server starts serving
        def on_serving_callback(actual_port):
            """Sends READY signal via queue when server is up."""
            # This function uses 'ready_queue' from the outer 'main' scope (closure)
            logger.info(f"Server is now serving on actual port: {actual_port}")
            if ready_queue: # Check if a queue object was provided
                try:
                    # Send message: "READY:<port>"
                    ready_queue.put(f"READY:{actual_port}")
                    logger.info("Sent READY signal via queue.")
                except Exception as q_err:
                    logger.error(f"Failed to put ready signal on queue: {q_err}")
            else:
                 # Log warning and print fallback if no queue (e.g., direct execution)
                 logger.warning("No ready_queue provided to send signal.")
                 # Fallback print just in case needed for specific test setups
                 print(f"KOLIBRI_SERVER_READY:{actual_port}", flush=True)

        # Subscribe the callback to the KolibriProcessBus 'SERVING' event
        kolibri_server.subscribe("SERVING", on_serving_callback)

        # --- Run Server ---
        logger.info("Starting Kolibri server main loop (kolibri_server.run())...")
        kolibri_server.run() # This call blocks until the server stops

        # --- Post Server Run ---
        logger.info("Kolibri server main loop finished (server stopped).")
        sys.exit(0) # Exit cleanly after server stops

    # --- Catch-all for Fatal Errors ---
    except Exception as e:
        logger.error(f"Fatal error encountered in server process main try block: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    freeze_support()

    logger.info("Server process script executed directly.")
    logger.info("Running main() without a ready_queue.")

    # Call the main function without a queue.
    main(ready_queue=None)
