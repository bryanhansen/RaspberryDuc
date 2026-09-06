import unittest

from raspberryduc.live import (
    can_payload,
    decode_rpm,
    decode_scrambler_rpm,
    decode_scrambler_tps,
    decode_temp_c,
    format_value,
    mode01_payload,
    parse_atrv,
)


class LiveDecodeTests(unittest.TestCase):
    def test_mode01_rpm(self):
        payload = mode01_payload(bytes.fromhex("410C1A90"), 0x0C)
        self.assertIsNotNone(payload)
        self.assertEqual(decode_rpm(payload), 1700.0)

    def test_coolant(self):
        self.assertEqual(decode_temp_c(bytes([0x5A])), 50.0)

    def test_scrambler_tps(self):
        self.assertAlmostEqual(decode_scrambler_tps(bytes([0xC8])), 100.0)

    def test_scrambler_rpm(self):
        self.assertEqual(decode_scrambler_rpm(bytes.fromhex("00E00A0BB8040001")), 3000.0)

    def test_format(self):
        self.assertEqual(format_value("rpm", 1500.4), "1500")
        self.assertEqual(format_value("batt_v", 12.56), "12.6")
        self.assertEqual(format_value("rpm", None), "—")

    def test_atrv(self):
        self.assertEqual(parse_atrv("12.4V"), 12.4)

    def test_can_payload_spaced(self):
        frame = can_payload("100 00 E0 0A 0B B8 04 00 01", 0x100)
        self.assertEqual(frame, bytes.fromhex("00E00A0BB8040001"))
