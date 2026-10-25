from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from ornith_launcher.catalog import gguf
from ornith_launcher.catalog.gguf import ArraySummary, GGUFError, NeedMoreBytes, ValueType
from tests.gguf_builder import gguf_bytes, ornith_pairs


class ParseMetadataTest(unittest.TestCase):
    def test_reads_scalars_strings_and_short_arrays(self):
        data = gguf_bytes(
            [
                ("a.u32", ValueType.UINT32, 7),
                ("a.i64", ValueType.INT64, -3),
                ("a.f32", ValueType.FLOAT32, 1.5),
                ("a.bool", ValueType.BOOL, True),
                ("a.text", ValueType.STRING, "Ornith 1.0 9B"),
                ("a.list", ValueType.ARRAY, (ValueType.INT32, [11, 11, 10, 0])),
            ],
            tensor_count=5,
        )
        meta = gguf.parse_metadata(data)
        self.assertEqual(meta.version, 3)
        self.assertEqual(meta.tensor_count, 5)
        self.assertTrue(meta.complete)
        self.assertEqual(
            dict(meta.values),
            {
                "a.u32": 7,
                "a.i64": -3,
                "a.f32": 1.5,
                "a.bool": True,
                "a.text": "Ornith 1.0 9B",
                "a.list": (11, 11, 10, 0),
            },
        )

    def test_long_arrays_are_summarised(self):
        tokens = [f"tok{i}" for i in range(gguf.MAX_KEPT_ARRAY + 1)]
        scores = [0.0] * 1000
        data = gguf_bytes(
            [
                ("tokenizer.ggml.tokens", ValueType.ARRAY, (ValueType.STRING, tokens)),
                ("tokenizer.ggml.scores", ValueType.ARRAY, (ValueType.FLOAT32, scores)),
                ("after", ValueType.UINT32, 1),
            ]
        )
        values = gguf.parse_metadata(data).values
        self.assertEqual(values["tokenizer.ggml.tokens"], ArraySummary(ValueType.STRING, 65))
        self.assertEqual(values["tokenizer.ggml.scores"], ArraySummary(ValueType.FLOAT32, 1000))
        self.assertEqual(values["after"], 1)  # skipping kept the reader in step

    def test_until_stops_early(self):
        data = gguf_bytes(ornith_pairs("35B"))
        meta = gguf.parse_metadata(data, until=gguf.stop_at_key_prefix("tokenizer."))
        self.assertTrue(meta.complete)  # the tokenizer key is the last pair here
        self.assertEqual(meta.architecture, "qwen35moe")

        meta = gguf.parse_metadata(data, until=gguf.stop_at_key_prefix("qwen35moe.block"))
        self.assertFalse(meta.complete)
        self.assertNotIn("qwen35moe.embedding_length", meta.values)

    def test_truncated_data_reports_bytes_needed(self):
        data = gguf_bytes(ornith_pairs("9B"))
        with self.assertRaises(NeedMoreBytes) as caught:
            gguf.parse_metadata(data[:100])
        self.assertGreater(caught.exception.needed, 100)
        with self.assertRaises(NeedMoreBytes):
            gguf.parse_metadata(b"GG")

    def test_rejects_non_gguf_data(self):
        with self.assertRaisesRegex(GGUFError, "magic"):
            gguf.parse_metadata(b"NOPE" + bytes(20))
        with self.assertRaisesRegex(GGUFError, "version 1"):
            gguf.parse_metadata(gguf_bytes([], version=1))

    def test_rejects_unknown_value_type(self):
        data = b"GGUF" + struct.pack("<IQQ", 3, 0, 1) + struct.pack("<Q", 1) + b"k"
        data += struct.pack("<I", 99)
        with self.assertRaisesRegex(GGUFError, "value type 99"):
            gguf.parse_metadata(data)

    def test_rejects_absurd_string_length(self):
        data = b"GGUF" + struct.pack("<IQQ", 3, 0, 1) + struct.pack("<Q", 2**40)
        with self.assertRaisesRegex(GGUFError, "corrupt"):
            gguf.parse_metadata(data)


class ReadMetadataTest(unittest.TestCase):
    def test_asks_for_longer_prefixes_until_parsing_succeeds(self):
        data = gguf_bytes(ornith_pairs("397B"))
        requested = []

        def fetch(size):
            requested.append(size)
            return data[:size]

        meta = gguf.read_metadata(fetch, sizes=(64, 256, len(data) + 10))
        self.assertEqual(meta.architecture, "qwen35moe")
        self.assertEqual(requested[-1], len(data) + 10)
        self.assertEqual(requested, sorted(requested))

    def test_skips_prefix_sizes_already_known_to_be_too_short(self):
        data = gguf_bytes([("x", ValueType.STRING, "y" * 500)])
        requested = []

        def fetch(size):
            requested.append(size)
            return data[:size]

        # 48 bytes reach the string's length field (500), so 64 and 128 are known to be too short.
        gguf.read_metadata(fetch, sizes=(48, 64, 128, 4096))
        self.assertEqual(requested, [48, 4096])

    def test_truncated_source_is_reported(self):
        data = gguf_bytes(ornith_pairs("9B"))[:200]
        with self.assertRaises(NeedMoreBytes):
            gguf.read_metadata(lambda size: data[:size], sizes=(64, 4096))

    def test_reads_from_a_local_file(self):
        data = gguf_bytes(ornith_pairs("9B", name="Ornith 1.0 9B"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model.gguf"
            path.write_bytes(data)
            meta = gguf.read_metadata(gguf.file_prefix_reader(path))
        self.assertEqual(meta.values["general.name"], "Ornith 1.0 9B")


if __name__ == "__main__":
    unittest.main()
