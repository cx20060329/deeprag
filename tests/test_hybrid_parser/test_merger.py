"""Tests for hybrid_parser/merger.py"""

import json
import os
import tempfile
import unittest

from hybrid_parser.merger import (
    MinerULoader, MinerUImage, MinerUTable, HybridDocumentMerger,
)


class TestMinerULoader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Use the real MinerU output
        cls.path = (
            r"D:\github\MinerU-master\output\PA2A_中央集控器"
            r"\PA2A_中央集控器20250813(1)\office"
            r"\PA2A_中央集控器20250813(1)_content_list.json"
        )
        if not os.path.exists(cls.path):
            raise unittest.SkipTest("MinerU content_list.json not found")
        cls.loader = MinerULoader(cls.path)

    def test_loads_images(self):
        self.assertGreater(self.loader.image_count, 0)
        self.assertIsInstance(self.loader.images[0], MinerUImage)

    def test_loads_tables(self):
        self.assertGreater(self.loader.table_count, 0)
        self.assertIsInstance(self.loader.tables[0], MinerUTable)

    def test_image_paths(self):
        for img in self.loader.images[:5]:
            self.assertTrue(img.img_path.startswith("images/"))

    def test_table_has_body(self):
        for tbl in self.loader.tables[:5]:
            self.assertTrue(tbl.table_body.startswith("<table>"))


class TestHybridDocumentMerger(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mineru_path = (
            r"D:\github\MinerU-master\output\PA2A_中央集控器"
            r"\PA2A_中央集控器20250813(1)\office"
            r"\PA2A_中央集控器20250813(1)_content_list.json"
        )
        if not os.path.exists(cls.mineru_path):
            raise unittest.SkipTest("MinerU content_list.json not found")

        cls.loader = MinerULoader(cls.mineru_path)
        cls.merger = HybridDocumentMerger(cls.loader)

        # Load Docling markdown
        docling_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "bcm_doc.md",
        )
        with open(docling_path, "r", encoding="utf-8") as f:
            cls.docling_md = f.read()

        cls.enhanced = cls.merger.enhance(cls.docling_md)

    def test_enhanced_larger_than_original(self):
        self.assertGreater(len(self.enhanced), len(self.docling_md))

    def test_image_placeholders_replaced(self):
        import re
        old = self.docling_md.count("<!-- image -->")
        new = len(re.findall(r"!\[图片\]\(images/", self.enhanced))
        # 41 MinerU images for 42 placeholders (1 may be unmatched)
        self.assertGreaterEqual(new, old - 1)

    def test_html_tables_injected(self):
        import re
        injected = len(re.findall(r"<!-- table_\d{3}", self.enhanced))
        self.assertGreater(injected, 0)

    def test_table_injection_has_rowspan(self):
        self.assertIn("rowspan", self.enhanced)

    def test_original_content_preserved(self):
        # Key Docling content should still be present
        self.assertIn("VMM", self.enhanced)
        self.assertIn("ExteriorLight", self.enhanced)

    def test_extract_html_tables(self):
        tables = HybridDocumentMerger.extract_html_tables_from_enhanced(
            self.enhanced,
        )
        self.assertGreater(len(tables), 0)
        for t in tables[:3]:
            self.assertIn("index", t)
            self.assertIn("html", t)
            self.assertTrue(t["html"].startswith("<table>"))


if __name__ == "__main__":
    unittest.main()
