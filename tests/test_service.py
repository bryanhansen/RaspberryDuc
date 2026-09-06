import unittest

from raspberryduc.service import (
    Indicator,
    OptionState,
    indicator_from_remaining,
    indicators_from_can_201,
    option_from_flag,
    parse_option_flag,
    parse_packed_service,
    parse_remaining,
)


class ServiceParseTests(unittest.TestCase):
    def test_remaining_due(self):
        self.assertEqual(indicator_from_remaining(0), Indicator.DUE)
        self.assertEqual(indicator_from_remaining(15000), Indicator.OK)

    def test_packed(self):
        data = bytes.fromhex("003A075C3A98")
        self.assertEqual(parse_packed_service(data), (58, 1884, 15000))

    def test_parse_remaining(self):
        self.assertEqual(parse_remaining(bytes.fromhex("3A98")), 15000)

    def test_can_201_clear(self):
        self.assertEqual(indicators_from_can_201(bytes(8)), (Indicator.OK, Indicator.OK))

    def test_can_201_oil_due(self):
        frame = bytes([0x01, 0, 0, 0, 0, 0, 0, 0])
        oil, desmo = indicators_from_can_201(frame)
        self.assertEqual(oil, Indicator.DUE)
        self.assertEqual(desmo, Indicator.OK)

    def test_grips_flag(self):
        self.assertEqual(parse_option_flag(bytes([0x01])), True)
        self.assertEqual(parse_option_flag(bytes([0x00])), False)
        self.assertEqual(option_from_flag(True), OptionState.ON)
        self.assertEqual(option_from_flag(False), OptionState.OFF)
