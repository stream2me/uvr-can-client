#!/usr/bin/env python3
"""
Example script: Using read_category to fetch specific UVR data.
"""

import sys
import os
import json

# Ensure we can import uvr_client from the parent directory
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from uvr_client import TA_UVR_CAN

def main():
    # Configuration
    INTERFACE = 'can0'
    REMOTE_NODE = 1   # The Node ID of your UVR
    LOCAL_NODE = 16   # Your script's Node ID

    # Using the Context Manager (with) ensures safe shutdown
    with TA_UVR_CAN(remote_node=REMOTE_NODE, local_node=LOCAL_NODE, channel=INTERFACE) as uvr:
        print(f"--- Starting UVR Read Example (Interface: {INTERFACE}) ---")
        try:
            if not uvr.connected:
                print("Failed to establish connection. Check CAN bus and Node IDs.")
                return

            print(f"--- UVR Data Collection (Node {REMOTE_NODE}) ---\n")

            # Identify Device
            model = uvr.is_uvr()
            print(f"Device Type: {model}\n")

            # --- EXAMPLE 1: Read specific fields from Inputs ---
            # We only want the numeric value and the health state (Short circuit, etc.)
            print("1. Fetching Inputs (Value & State):")
            input_data = uvr.read_category("input", ["value", "state"])
            print(json.dumps(input_data, indent=4, ensure_ascii=False))

            print("\n" + "-"*40 + "\n")

            # --- EXAMPLE 2: Read specific fields from Outputs ---
            # We want to know the mode (Auto/Manual) and the current state (On/Off/Speed)
            print("2. Fetching Outputs (Mode & State):")
            output_data = uvr.read_category("output", ["mode", "state"])
            print(json.dumps(output_data, indent=4, ensure_ascii=False))

            print("\n" + "-"*40 + "\n")

            # --- EXAMPLE 3: Read all available fields for Device Info ---
            # Using "all" or None retrieves all fields defined in IDX_CONFIG
            print("3. Fetching Device Information (All fields):")
            device_info = uvr.read_category("uvr", "all")
            print(json.dumps(device_info, indent=4, ensure_ascii=False))

        except KeyboardInterrupt:
            print("\nStopped by user.")
        except Exception as e:
            print(f"\nAn error occurred: {e}")

if __name__ == "__main__":
    main()
