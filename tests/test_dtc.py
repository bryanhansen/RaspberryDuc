import unittest

from raspberryduc.dtc import decode_mode03, decode_uds_dtc, negative_response, sae_code_from_bytes


class DtcTests(unittest.TestCase):
    def test_sae_p0133(self):
        self.assertEqual(sae_code_from_bytes(0x01, 0x33), "P0133")

    def test_mode03_single(self):
        codes = decode_mode03(bytes.fromhex("430133"))
        self.assertEqual([c.code for c in codes], ["P0133"])

    def test_mode03_none(self):
        self.assertEqual(decode_mode03(bytes.fromhex("430000")), [])

    def test_negative_response_not_dtc(self):
        self.assertTrue(negative_response(bytes.fromhex("7F0311")))
        self.assertEqual(decode_mode03(bytes.fromhex("7F0311")), [])
        self.assertEqual(decode_mode03(bytes.fromhex("7F0711")), [])

    def test_uds_dtc(self):
        codes = decode_uds_dtc(bytes.fromhex("5902FF01330001"))
        self.assertEqual(codes[0].raw, "013300")


if __name__ == "__main__":
    unittest.main()
