# TA_UVR_CAN
A Python library for communicating with **Technische Alternative (TA)** devices (such as UVR1611, UVR16x2) via CAN bus (SocketCAN).

## 🚀 Features
* **SocketCAN Integration:** Reliable communication using the `python-can` library.
* **TA Login Protocol:** Implements the proprietary TA handshake (0x400 ID range) required for communication.
* **SDO Support:** Handles both **Expedited** and **Segmented** SDO transfers for reading parameters, names, and values.
* **Smart Parsing:** Automatically converts TA-specific data types (temperatures, units, bitfields, and states).
* **Differential Updates:** Efficiently syncs dynamic values while caching static metadata.
* **Robustness:** Includes retry logic and timeout handling for stable bus operations.

## 🛠 Prerequisites
* **Hardware:** A Linux-supported CAN interface (e.g., MCP2515, Waveshare CAN Hat).
* **OS:** Linux (required for SocketCAN).
* **Python:** 3.9 or higher.

## 📦 Installation
```bash
# Clone the repository
git clone https://github.com/stream2me/uvr-can-client.git
cd uvr-can-client

# Install dependencies
pip install python-can
```

## 💻 Quick Start
```bash
from uvr_client import TA_UVR_CAN

# Initialize the connection (UVR is Node 1, your PC/Pi is Node 16)
with TA_UVR_CAN(remote_node=1, local_node=16, channel='can0') as uvr:
    # Identify the device
    model = uvr.is_uvr()
    print(f"Device identified as: {model}")

    # get device details
    data = uvr.read_category("uvr")
    print(f"Connected to: {data['output']['name']} (HW: {data['output']['hw']})")

```
<sup>see examples for more<sup/>

## 📊 Advanced Monitoring
The library is designed to minimize bus load. You can perform a "Full Scan" once to get all names and types, and then only update "Value" and "State" fields in a loop.
```bash
# Periodic update example
data_fragments = [
    uvr.read_category("input", ["value", "state"]),
    uvr.read_category("output", ["state"])]
# Deep merge into master state
my_uvr = uvr.merge_uvr_data(*data_fragments)
```

## 📐 Data Structure
The library returns a nested dictionary structured by categories. Note that `analog_out` is automatically mapped into the `output` category for a unified API:
* `input`: Indices 1-16
* `output`: Indices 1-14 (Digital/PWM) and 15-16 (Analog)

## 🤝 Credits & Acknowledgments
This project is a Python implementation inspired by and based on the logic of the [brutella/uvr](https://github.com/brutella/uvr) Go library. Special thanks to the original authors for their work on reverse-engineering the TA CAN protocol.

## ⚠️ Disclaimer
**IMPORTANT:** This project is not affiliated with, nor endorsed by, Technische Alternative RT GmbH. Heating systems are safety-critical. Use this software at your own risk. The author assumes no responsibility for any damage to your hardware or home.

## 📄 License
This project is licensed under the MIT License.
