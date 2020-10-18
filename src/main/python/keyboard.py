# SPDX-License-Identifier: GPL-2.0-or-later

import struct
import json
import lzma
from collections import OrderedDict

from kle_serial import Serial as KleSerial
from util import MSG_LEN, hid_send


CMD_VIA_GET_KEYCODE = 0x04
CMD_VIA_SET_KEYCODE = 0x05
CMD_VIA_GET_LAYER_COUNT = 0x11
CMD_VIA_VIAL_PREFIX = 0xFE

CMD_VIAL_GET_KEYBOARD_ID = 0x00
CMD_VIAL_GET_SIZE = 0x01
CMD_VIAL_GET_DEFINITION = 0x02


class Keyboard:
    """ Low-level communication with a vial-enabled keyboard """

    def __init__(self, dev, usb_send=hid_send):
        self.dev = dev
        self.usb_send = usb_send

        # n.b. using OrderedDict here to make order of layout requests consistent for tests
        self.rowcol = OrderedDict()
        self.layout = dict()
        self.rows = self.cols = self.layers = 0
        self.keys = []

        self.vial_protocol = self.keyboard_id = -1

    def reload(self, sideload_json=None):
        """ Load information about the keyboard: number of layers, physical key layout """

        self.rowcol = OrderedDict()
        self.layout = dict()

        self.reload_layout(sideload_json)
        self.reload_layers()
        self.reload_keymap()

    def reload_layers(self):
        """ Get how many layers the keyboard has """

        self.layers = self.usb_send(self.dev, struct.pack("B", CMD_VIA_GET_LAYER_COUNT))[1]

    def reload_layout(self, sideload_json=None):
        """ Requests layout data from the current device """

        if sideload_json is not None:
            payload = sideload_json
        else:
            # get keyboard identification
            data = self.usb_send(self.dev, struct.pack("BB", CMD_VIA_VIAL_PREFIX, CMD_VIAL_GET_KEYBOARD_ID))
            self.vial_protocol, self.keyboard_id = struct.unpack("<IQ", data[0:12])

            # get the size
            data = self.usb_send(self.dev, struct.pack("BB", CMD_VIA_VIAL_PREFIX, CMD_VIAL_GET_SIZE))
            sz = struct.unpack("<I", data[0:4])[0]

            # get the payload
            payload = b""
            block = 0
            while sz > 0:
                data = self.usb_send(self.dev, struct.pack("<BBI", CMD_VIA_VIAL_PREFIX, CMD_VIAL_GET_DEFINITION, block))
                if sz < MSG_LEN:
                    data = data[:sz]
                payload += data
                block += 1
                sz -= MSG_LEN

            payload = json.loads(lzma.decompress(payload))

        self.rows = payload["matrix"]["rows"]
        self.cols = payload["matrix"]["cols"]

        serial = KleSerial()
        kb = serial.deserialize(payload["layouts"]["keymap"])

        self.keys = kb.keys

        for key in self.keys:
            key.row = key.col = None
            if key.labels[0] and "," in key.labels[0]:
                row, col = key.labels[0].split(",")
                row, col = int(row), int(col)
                key.row = row
                key.col = col
                self.rowcol[(row, col)] = True

    def reload_keymap(self):
        """ Load current key mapping from the keyboard """

        for layer in range(self.layers):
            for row, col in self.rowcol.keys():
                data = self.usb_send(self.dev, struct.pack("BBBB", CMD_VIA_GET_KEYCODE, layer, row, col))
                keycode = struct.unpack(">H", data[4:6])[0]
                self.layout[(layer, row, col)] = keycode

    def set_key(self, layer, row, col, code):
        key = (layer, row, col)
        if self.layout[key] != code:
            self.usb_send(self.dev, struct.pack(">BBBBH", CMD_VIA_SET_KEYCODE, layer, row, col, code))
            self.layout[key] = code

    def save_layout(self):
        """ Serializes current layout to a binary """

        # TODO: increase version before release
        data = {"version": 0}
        layout = []
        for l in range(self.layers):
            layer = []
            layout.append(layer)
            for r in range(self.rows):
                row = []
                layer.append(row)
                for c in range(self.cols):
                    val = self.layout.get((l, r, c), -1)
                    row.append(val)
        data["layout"] = layout
        # TODO: this should also save/restore macros, when implemented
        return json.dumps(data).encode("utf-8")

    def restore_layout(self, data):
        """ Restores saved layout """

        data = json.loads(data.decode("utf-8"))
        for l, layer in enumerate(data["layout"]):
            for r, row in enumerate(layer):
                for c, col in enumerate(row):
                    if (l, r, c) in self.layout:
                        self.set_key(l, r, c, col)
