import can
import struct
import time
import logging
from typing import Dict, Any, Optional

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

# Based on the protocol definitions by Matthias Hochgatterer (https://github.com/brutella/uvr)
class TA_UVR_CAN:
    """
    Class for communication with Technical Alternative (TA) devices (UVR1611, UVR16x2)
    via CANopen SDO transfer.
    """

    CONFIG = {
        "input": {
            "count": 16,
            "name": {"index": 0x2084}
        },
        "output": {
            "count": 14,
            "name": {"index": 0x20A5},
            "type": {"index": 0x20a0}
        }
    }

    IDX_CONFIG = {
        "uvr": {
            "count": 0,
            "fields": {
                "name": 0x1008,
                "hw": 0x1009,
                "sw": 0x100A
            }
        },
        "input": {
            "count": 16,
            "fields": {
                "name": 0x2084,
                "value": 0x208D,
                "state": 0x208E
            }
        },
        "output": {
            "count": 14,
            "fields": {
                "name": 0x20A5,
                "type": 0x20A0,
                "mode": 0x20A1,
                "state": 0x20AA
            }
        },
        "analog_out": {
            "count": 2, # out 15 and 16
            "offset": 14, # start at Index 15
            "fields": {
                "name": 0x20C1,
                "mode": 0x20C5,
                "value": 0x20C9
            }
        }
    }

    ABORT_TEXT = {
        0x05040000: "SDO protocol timed out",
        0x06010000: "Unsupported access to an object",
        0x06010001: "Attempt to read a write only object",
        0x06010002: "Attempt to write a read only object",
        0x06020000: "Object does not exist in the object dictionary",
        0x06040041: "Object cannot be mapped to the PDO",
        0x06040042: "The number and length of the objects to be mapped would exceed PDO length",
        0x06070010: "Data type length mismatch",
        0x06070012: "Object dictionary does not match the PDO length",
        0x06090011: "Subindex does not exist",
        0x06090030: "Value range of parameter exceeded",
        0x06090031: "Value of parameter written too high",
        0x06090032: "Value of parameter written too low",
        0x08000000: "General error",
    }

    def __init__(self, remote_node=1, local_node=16, channel='can0', interface='socketcan'):
        self.channel = channel
        self.interface = interface
        self.bus = None
        self.remote_node = remote_node
        self.local_node = local_node
        self.sdo_id = 0
        self.hb = None
        self.connected = False
        self.uvr_data = {}

        # Payload template for SDO Login Request
        self.payload = bytearray(8)
        self.payload[0] = 0x80 | self.remote_node & 0x7F
        self.payload[1] = 0x00  # activate
        self.payload[2] = 0x1F
        self.payload[3] = 0x00
        self.payload[4] = 0x00 | self.remote_node & 0x7F # Server node id
        self.payload[5] = 0x00 | self.local_node & 0x7F  # Client node id
        self.payload[6] = 0x80
        self.payload[7] = 0x12

    def __enter__(self):
        self.bus = can.interface.Bus(channel=self.channel, interface=self.interface)
        self.connected = self.set_activ(True)
        if not self.connected:
            log.error("No connection to Node")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.bus:
            try:
                if self.connected: self.set_activ(False)
            except Exception as e:
                log.warning(f"UVR connection could not be properly disconnected.: {e}")
            self.bus.shutdown()
            log.info("CAN bus safely shut down.")

    def request_response(self, tx_id, payload, rx_id, timeout=1):
        """Sends message and waits for specific response ID."""
        self.bus.set_filters([{"can_id": rx_id, "can_mask": 0x7FF}])

        try:
            # flush buffer (delete old messages)
            while self.bus.recv(timeout=0): pass

            # send request
            msg = can.Message(arbitration_id=tx_id, data=payload, is_extended_id=False)
            self.bus.send(msg)

            # Awaiting response
            start_time = time.monotonic()
            while (time.monotonic() - start_time) < timeout:
                res = self.bus.recv(timeout=0.1)
                if res and res.is_rx and res.dlc == 8:
                    return res

        except can.CanError as e:
            log.error(f"CAN-Bus Error: {e}")
        finally:
            self.bus.set_filters(None)

        return None

    def abort_text(self, code: int) -> str:
        return self.ABORT_TEXT.get(code, f"Unknown ErrorCode: {hex(code)}")

    def sdo_upload_seg_wrapper(self, index, subindex, timeout=1.5, retries=3):
        """Segmented SDO upload wrapper."""
        for attempt in range(retries + 1):
            try:
                # CS=0x40 (Upload Request)
                data = [0x40, index & 0xFF, (index >> 8) & 0xFF, subindex, 0, 0, 0, 0]

                # Calculate SDO ID dynamically.
                # Here we use self.sdo_id, which is set during login.
                if self.sdo_id == 0:
                    raise Exception("COB is not set")
                req_id = self.sdo_id
                res_id = req_id - 128 # 0x580 + NodeID

                res = self.request_response(req_id, data, res_id, timeout=timeout)
                if not res:
                    raise TimeoutError("Initial SDO Timeout")

                # Check SDO Abort (0x80)
                if res.data[0] == 0x80:
                    # Abort frame: [0x80, idx_lo, idx_hi, sub, abort0..3]
                    abort_code = struct.unpack("<I", bytes(res.data[4:8]))[0]

                    if abort_code == 0x06020000 or abort_code == 0x06090011:
                         return None
                    raise ValueError(self.abort_text(abort_code))

                # Check Expedited Transfer (data directly in frame)
                if res.data[0] & 0x02:
                    # Bits 2&3 encode the number of empty bytes (4 - n = data size)
                    size = 4 - ((res.data[0] >> 2) & 0x03)
                    return res.data[4:4+size]

                # Segmented Transfer
                all_data = bytearray()
                toggle = 0

                while True:
                    # Toggle-Bit (Bit 4): Request 0x60 or 0x70
                    cmd = 0x60 | (toggle << 4)
                    res = self.request_response(req_id, [cmd] + [0]*7, res_id, timeout=timeout)

                    if not res: raise TimeoutError("Segment Timeout")
                    if res.data[0] == 0x80:
                        abort_code = struct.unpack('<I', res.data[4:8])[0]
                        raise ValueError("SDO Abort in Segment", self.abort_text(abort_code))

                    scs = res.data[0]

                    # Bits 1-3: n (number of bytes without data) -> Data is 7 - n
                    num_bytes = 7 - ((scs >> 1) & 0x07)
                    all_data.extend(res.data[1:1+num_bytes])

                    # Bit 0 is "End"-Flag (c=1)
                    if scs & 0x01:
                        return bytes(all_data)

                    toggle ^= 1 # flip Bit for next Segment

            except (TimeoutError, ValueError, can.CanError) as e:
                if attempt < retries:
                    log.debug(f"Attempt {attempt+1} failed: {e}. Retry...")
                    time.sleep(0.2)
                else:
                    log.debug(f"SDO Error {hex(index)}:{subindex} -> {e}")
                    return None
        return None

    def heartbeat(self):
        msg = can.Message(arbitration_id=0x700 + self.local_node, data=[0x05], is_extended_id=False)
        return self.bus.send_periodic(msg, 10)

    def set_activ(self, activate: bool) -> bool:
        """
        Start or stop connection to remote Node,
        activate=True  -> Request connection (0x00),
        activate=False -> End connection (0x01)
        """
        tx_id = 0x400 + self.local_node
        rx_id = 0x400 + self.remote_node
        payload = list(self.payload)
        payload[1] = 0x00 if activate else 0x01

        rx = self.request_response(tx_id, payload, rx_id)

        if rx and rx.data[0] == (0x80 + self.local_node):
            if activate:
                # Connection established
                self.sdo_id = struct.unpack('<H', rx.data[4:6])[0]
                self.hb = self.heartbeat()
                log.info(f"Connection established: COB {hex(self.sdo_id)}")
            else:
                # End connection
                hb = getattr(self, 'hb', None)
                if hb:
                    try: hb.stop()
                    except: pass
                    self.hb = None
                log.debug("Connection terminated")
            return True

        log.error(f"CRITICAL ERROR: {'Activation' if activate else 'Deactivation'} failed.")
        log.debug(f"Response from node: {rx}")
        return False

    def read_data(self, index: int, subindex: int) -> Optional[Any]:
        b = self.sdo_upload_seg_wrapper(index, subindex)
        if b is None:
            return None

        # filter standard canopen frames
        if index < 0x2000:
            if len(b) <= 4:
                val = int.from_bytes(b, 'little')
                return f"0x{val:02X}"
            return self.parseString(index, subindex, b)

        # handle device specific frames
        if len(b) < 7:
            val = int.from_bytes(b, 'little')
            return f"0x{val:02X}"

        # get Type from Byte 6 (Bits 4-6)
        dataType = b[6] & 0x70
        is_parameter = (b[6] & 0x80) != 0
        
        if dataType == 0x00 and not is_parameter:
            # We read bytes 2 and 3 as a 16-bit value (little endian)
            # b[0] is byte 2 (outputs 1-8), b[1] is byte 3 (outputs 9-14+)
            val = struct.unpack('<H', b[0:2])[0]
            out_status = {}
            for i in range(14):
                # Check whether the i-th bit is set
                out_status[f"Ausgang_{i+1}"] = bool(val & (1 << i))
            return out_status

        try:
            match dataType:
                case 0x10:  # String Reference
                    idx_ref = struct.unpack('<H', b[4:6])[0]
                    sub_ref = b[0]
                    val = self.parseString(idx_ref, sub_ref)
                    return val if val and val.strip("-") else None

                case 0x20:  # Bit field
                    #return self.parseBits(b)
                    return f"{b[0]:b}"

                case 0x30:  # Character
                    log.debug("DT is Char")
                    val = self.parseCharacter(b)
                    return val if val != "unbenutzt" else "unbenutzt"

                case 0x40:  # 16-bit integer -> float
                    log.debug("DT is int16")
                    return self.parseInt16(b)

                case 0x50:  # 32-bit integer -> float
                    log.debug("DT is int32")
                    return self.parseInt32(b)

                case _:
                    log.debug(f"Unknown Type {hex(dataType)} at {hex(index)}")
                    log.debug(f"RAW Data: {b}")
                    return None
        except Exception as err:
            log.error(f"Parse Error {hex(index)}:{subindex}]: {err}")
            return None

    def parseBits(self, b):
            data_width = b[6] & 0x0F

            # String table index from A1 (b[5]) and A0 (b[4])
            # A1 is MSB, A0 is LSB
            idx = struct.unpack('>H', b[4:6])[0]

            # The actual data is in D0 (column 2 -> b[0])
            raw_data = b[0]
            bit_results = {}
            for i in range(data_width):
                # 0 = normal display, 1 = inverse display according to documentation
                is_set = bool(raw_data & (1 << i))

                sub_idx = i +1 # Subindex = Bitnr. + 1
                string = self.parseString(idx, sub_idx)

                bit_results[f"Bit_{i}"] = {
                    "active": is_set,
                    "string": string
                }

            return {
                "type": "Bitfeld",
                "width": data_width,
                "states": bit_results
            }

    def parseString(self, index, subindex, b=None) -> Optional[str]:
        if b is None:
            raw = self.sdo_upload_seg_wrapper(index, subindex)
        else: raw = b
        # map non standard symbols
        mapping = str.maketrans({'ü': 'ö'})
        if raw and b'\x00' in raw:
            string = raw.split(b'\x00')[0].decode('CP437', errors='ignore').strip()
            return string.translate(mapping).strip()
        return None

    def parseCharacter(self, b):
        spec = b[6] & 0x7F
        value = b[0]
        floatValue = float(value)
        decimal = b[4] & 0x0F

        if decimal > 0: floatValue /= (10.0 ** decimal)

        match spec:
            case 0x32: return "AUS" if value == 0 else floatValue # Digital
            case 0x33: return value
            case 0x34: return chr(0x41 + value) if value <= 25 else None
            case 0x35: return value * 5
            case 0x36: # Times
                if value <= 90: return f"{value}s"
                if value <= 107: return f"{(value - 87) * 30}s"
                if value <= 157: return f"{(value - 97) * 60}s"
                return f"{(value - 155) * 1800}s"
            case 0x3A: return value * 10
            case _:
                log.debug(f"Unsupported character data type %X", {(b[6]&0x07)})
                return None

    def parseInt32(self, b):
        '''parses a 32-bit integer and converts it to a 32-bit float'''
        unit = self.parseUnit(b[5])
        decimal = b[4] & 0x0F
        v = struct.unpack('<i', bytes(b[0:4]))[0]
        float_val = float(v) / (10.0 ** decimal) if decimal > 0 else float(v)
        return {"value": float_val, "unit": unit}

    def parseInt16(self, b):
        '''parses a 16-bit integer and converts it to a 32-bit float'''
        unit = self.parseUnit(b[5])
        decimal = b[4] & 0x0F
        v = struct.unpack('<h', bytes(b[0:2]))[0]
        float_val = float(v) / (10.0 ** decimal) if decimal > 0 else float(v)
        return {"value": float_val, "unit": unit}

    def parseUnit(self, b_val):
        units = ["", "°C", "W/m²", "l/h", "sec", "min", "K", "%", "%"]
        if 0 <= b_val < len(units):
            return units[b_val]
        return "nan"

    def is_uvr(self) -> str:
        '''Checks the identity of the UVR node (1611 or 16x2) and returns the type, if it exists.'''
        vendor_raw = self.sdo_upload_seg_wrapper(0x1018, 1)
        prod_raw = self.sdo_upload_seg_wrapper(0x1018, 2)

        if not vendor_raw or not prod_raw: return "unknown Device"

        vendor = vendor_raw[0]
        # Shifting bytes correctly for 16-bit product ID
        product = (prod_raw[1] << 8) | prod_raw[0] if len(prod_raw) >= 2 else 0

        if vendor == 0xCB: # Technische Alternative
            if product == 0x100B: return "uvr1611"
            if product == 0x01: return "uvr16x2"
            return f"TA Device (ID: {hex(product)})"
        log.debug(f"Node {self.remote_node}: no TA Device (Vendor: {hex(vendor)})")
        return "Generic CANopen Node"

    def read_time(self):
        '''Returns the local time and date from the UVR1611.'''
        try:
            mm = self.sdo_upload_seg_wrapper(0x2011, 0x01)
            hh = self.sdo_upload_seg_wrapper(0x2012, 0x01)
            d = self.sdo_upload_seg_wrapper(0x2014, 0x01)
            m = self.sdo_upload_seg_wrapper(0x2015, 0x01)
            y = self.sdo_upload_seg_wrapper(0x2016, 0x01)

            if all(val is not None and len(val) > 0 for val in [d, m, y, hh, mm]):
                return f"{hh[0]:02d}:{mm[0]:02d} {d[0]:02d}-{m[0]:02d}-{2000 + y[0]}"
        except Exception as err:
            log.debug(f"Error retrieving the time -> {err}")
            return None

    def read_names(self) -> Dict[str, Dict[int, Any]]:
        results = {}
        for category, settings in self.CONFIG.items():
            results[category] = {}
            fields = {k: v["index"] for k, v in settings.items() if k != "count"}

            for i in range(1, settings["count"] + 1):
                entry = {f: self.read_data(idx, i) for f, idx in fields.items()}

                if entry.get("type") == "unbenutzt":
                    results[category][i] = "unbenutzt"
                else:
                    results[category][i] = entry
        return results

    def read_1611_in(self) -> Dict[str, Dict[int, Any]]:
        """Reads values and states of inputs."""
        data = {}

        # Index Mapping
        idx_val = 0x208D # Value and unit
        idx_state = 0x220B # State (OK, Kurzschluss ...)

        for i in range(16):
            input_num = i + 1
            entry = {}

            # 1. Value and unit
            val_data = self.read_data(idx_val, 0x01 + i)
            if isinstance(val_data, dict):
                entry['value'] = val_data.get('value')
                entry['unit'] = val_data.get('unit')
            else:
                entry['value'] = val_data

            # 2. State
            state = self.read_data(idx_state, 0x11 + i)
            if state:
                entry['state'] = state

            data[input_num] = entry

        return {"input": data}

    def read_1611_out(self) -> Dict[str, Dict[int, Any]]:
        ''' read output 1 to 14'''
        # "state": {"index": 0x20ac}
        data = {}
        for i in range(1, 15):
            entry = {}
            # Mode (Hand/Auto)
            mode = self.read_data(0x20a1, i)
            if mode: entry['mode'] = mode

            # Status (Ein/Aus/Drehzahl)
            state = self.read_data(0x20aa, i)
            if state: entry['state'] = state

            data[i] = entry
        return {"output": data}

    def read_1611_analogOut(self):
        data = {}
        for i in range(15, 17):
            entry = {}
            mode = self.read_data(0x20c5, i)
            val = self.read_data(0x20c9, i)

            if mode: entry['mode'] = mode
            if val: entry['value'] = val

            data[i] = entry
        return {"output": data}

    def merge_uvr_data(self, *update_dicts):
        """
        update the internal data object that stores the uvr data
        """
        if self.uvr_data is None:
            self.uvr_data = {}

        for update_dict in update_dicts:
            if not update_dict or not isinstance(update_dict, dict):
                continue
            for category, items in update_dict.items():
                if category == "analog_out":
                    category = "output"
                if category not in self.uvr_data:
                    self.uvr_data[category] = {}

                for i, values in items.items():
                    # If the index is new, or one of the two values is not a dict (e.g., ‘unbenutzt’),
                    # we simply overwrite the entry completely.
                    if (i not in self.uvr_data[category] or
                        not isinstance(self.uvr_data[category][i], dict) or
                        not isinstance(values, dict)):
                        self.uvr_data[category][i] = values
                    else:
                        # If both are dictionaries, we update the fields.
                        self.uvr_data[category][i].update(values)

        return self.uvr_data

    def read_category(self, category: str, include_fields: list = None):
        """Reads all entries of a category (e.g., 'input')."""
        conf = self.IDX_CONFIG.get(category)
        if not conf: return {}
        results = {category: {}}
        offset = conf.get("offset", 0)

        if include_fields is None or include_fields == "all":
            include_fields = list(conf["fields"].keys())

        s = 0 if conf["count"] == 0 else 1

        for i in range(s, conf["count"] + 1):
            subindex = i + offset
            entry = {}
            #if type == "unbenutzt"  the output is not in use
            type_idx = conf["fields"].get("type")
            if type_idx:
                type_val = self.read_data(type_idx, subindex)
                if type_val == "unbenutzt":
                    results[category][subindex] = "unbenutzt"
                    continue
                if type_val: entry["type"] = type_val

            if include_fields:
                for field in include_fields:
                    if field == "type": continue

                    idx = conf["fields"].get(field)
                    if idx:
                        val = self.read_data(idx, subindex)
                        if val is None: continue
                        if isinstance(val, dict):
                            entry.update(val)
                        else:
                            entry[field] = val
            if entry and category == "uvr":
                entry.update({
                    "node": self.remote_node,
                    "time": self.read_time()
                })
                results[category] = entry
                continue
            if entry: results[category][subindex] = entry
        return results
