#!/usr/bin/env python3
"""
Advanced monitoring script for TA UVR1611/16x2 devices.
Efficiently updates changing values while periodically refreshing static metadata.
"""

import sys
import os
import time
import pprint
import logging

# Add parent directory to path so we can import uvr_client
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from uvr_client import TA_UVR_CAN

# --- Logging Configuration ---
# Set log level to ERROR to keep the console clean from INFO/DEBUG messages
logging.basicConfig(
    format="{asctime} - {levelname} - {message}",
    style="{",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.ERROR
)

# --- Configuration ---
INTERVAL = 60           # Seconds between updates
FULL_SCAN_CYCLES = 10   # Refresh metadata every X cycles
REMOTE_NODE = 1
LOCAL_NODE = 16
INTERFACE = 'can0'

def main():
    cycle = 0
    my_uvr_state = {}

    print(f"--- UVR Monitoring Started (Node {REMOTE_NODE}) ---")
    print(f"Interval: {INTERVAL}s, Full scan every {FULL_SCAN_CYCLES} cycles.")
    print("Press Ctrl+C to exit.\n")

    try:
        with TA_UVR_CAN(remote_node=REMOTE_NODE, local_node=LOCAL_NODE, channel=INTERFACE) as uvr:
            # Check if login was successful (sdo_id is set)
            if not uvr.sdo_id:
                print("Error: Could not establish a session with the UVR.")
                return

            # watch for heartbeat
            while uvr.hb:
                is_full_scan = (cycle % FULL_SCAN_CYCLES == 0)
                timestamp = time.strftime('%H:%M:%S')

                try:
                    if is_full_scan:
                        print(f"[{timestamp}] Full Scan: Fetching all metadata and values...")
                        # Categories to fetch completely
                        categories = ["uvr", "input", "output", "analog_out"]
                        data = [uvr.read_category(cat, "all") for cat in categories]
                    else:
                        print(f"[{timestamp}] Delta Scan: Updating dynamic values...", end="\r")
                        # Only fetch fields that change frequently
                        data = [
                            uvr.read_category("input", ["value", "state"]),
                            uvr.read_category("output", ["state"]),
                            uvr.read_category("analog_out", ["value"])
                        ]

                    # Update our master state object
                    # merge_uvr_data should handle updating existing keys
                    my_uvr_state = uvr.merge_uvr_data(*data)

                    # Visual separator and output
                    print("\n" + "="*100)
                    pprint.pprint(my_uvr_state, width=120, indent=2)
                    print("="*100 + "\n")

                except Exception as e:
                    print(f"\n[{timestamp}] Communication error: {e}")
                    # Optional: Re-login if connection is lost
                    # if not uvr.set_active(True): break

                cycle += 1
                time.sleep(INTERVAL)

    except KeyboardInterrupt:
        print("\nMonitoring stopped by user.")
    except Exception as e:
        print(f"\nCritical system error: {e}")

    print("\nProgram terminated.")

if __name__ == "__main__":
    main()
