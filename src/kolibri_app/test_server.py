"""
Utility script to test the server process (_server_process.py) directly,
bypassing the main wxPython GUI application. Useful for isolated debugging.

This script sets the necessary environment variables based on command-line
arguments and then directly invokes the main function of the server process module.

Usage:
    python -m kolibri_app.test_server [--port <http_port>] [--zip-port <zip_port>]

Note:
  - Ensure Kolibri and its dependencies are installed in the environment.
  - KOLIBRI_HOME should be set in your environment if you don't want to use
    the default (~/.kolibri).
  - This script explicitly sets DJANGO_SETTINGS_MODULE.
"""

import os
import sys
import argparse
import logging

# Configure basic logging for this test script itself
logging.basicConfig(level=logging.INFO, format="%(levelname)s (test_server): %(message)s")

def main():
    parser = argparse.ArgumentParser(
        description='Test Kolibri server process (_server_process.main)',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        '--port',
        type=int,
        default=None,
        help='Specify the HTTP port for Kolibri. Overrides Kolibri default (0=auto).'
    )
    parser.add_argument(
        '--zip-port',
        type=int,
        default=None,
        help='Specify the ZIP Content port. Overrides Kolibri default.'
    )
    args = parser.parse_args()

    logging.info("Preparing to launch Kolibri server process...")

    # --- Environment Setup ---
    # Ensure essential environment variables are set *before* importing/running
    # the server process code, which relies on them.

    # 1. Set Django Settings Module (usually done in src/kolibri_app/__init__.py)
    os.environ["DJANGO_SETTINGS_MODULE"] = "kolibri_app.django_app_settings"
    logging.info(f"Set DJANGO_SETTINGS_MODULE='{os.environ['DJANGO_SETTINGS_MODULE']}'")

    # 2. Set Ports via Environment Variables (if specified by user)
    #    _server_process.py reads these environment variables.
    if args.port is not None:
        os.environ["KOLIBRI_HTTP_PORT"] = str(args.port)
        logging.info(f"Set KOLIBRI_HTTP_PORT='{args.port}' from command line argument.")
    else:
        logging.info("Using default Kolibri HTTP port (likely 0 for auto-select).")

    if args.zip_port is not None:
        os.environ["KOLIBRI_ZIP_CONTENT_PORT"] = str(args.zip_port)
        logging.info(f"Set KOLIBRI_ZIP_CONTENT_PORT='{args.zip_port}' from command line argument.")
    else:
        logging.info("Using default Kolibri ZIP Content port.")

    # 3. Check KOLIBRI_HOME (inform user)
    #    This is usually set by the user's environment or the runtime hook
    #    in a packaged app.
    if "KOLIBRI_HOME" not in os.environ:
        default_home = os.path.join(os.path.expanduser("~"), ".kolibri")
        logging.warning(f"KOLIBRI_HOME environment variable not set. Kolibri will use default: {default_home}")
    else:
        logging.info(f"Using KOLIBRI_HOME='{os.environ['KOLIBRI_HOME']}'")

    # --- Import and Run Server ---
    try:
        # Import the server process entry point *after* setting environment variables
        from kolibri_app._server_process import main as server_main

        logging.info("Starting _server_process.main()... (This will block until server exits)")

        # The _server_process.main function expects a 'ready_queue' argument.
        # Since we are running it directly without a parent process waiting on a queue,
        # we pass None. The server process checks if the queue exists before using it.
        server_main(ready_queue=None)

        logging.info("_server_process.main() finished.")
        sys.exit(0)

    except ImportError as e:
         logging.error(f"Failed to import server dependencies: {e}", exc_info=True)
         logging.error("Ensure Kolibri and its dependencies are installed correctly in the environment.")
         sys.exit(1)
    except Exception as e:
        # Catch potential errors during server_main execution itself
        logging.error(f"An error occurred during server execution: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    # No need for freeze_support() here, as this script isn't the one being frozen,
    # and _server_process.py calls it if needed when it starts.
    main()