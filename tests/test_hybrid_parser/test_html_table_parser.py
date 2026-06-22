"""Tests for hybrid_parser/html_table_parser.py"""

import unittest
from hybrid_parser.html_table_parser import HtmlTableParser


class TestHtmlTableParser(unittest.TestCase):
    def setUp(self):
        self.parser = HtmlTableParser()

    def test_simple_table(self):
        html = (
            "<table><tr><th>A</th><th>B</th></tr>"
            "<tr><td>1</td><td>2</td></tr>"
            "<tr><td>3</td><td>4</td></tr></table>"
        )
        result = self.parser.parse(html)
        self.assertEqual(result.headers, ["A", "B"])
        self.assertEqual(len(result.rows), 2)
        self.assertEqual(result.rows[0], ["1", "2"])
        self.assertFalse(result.has_rowspan)

    def test_rowspan_table(self):
        html = (
            "<table><tr><th>Signal</th><th>CAN ID</th></tr>"
            '<tr><td rowspan="2">PEPS_UsageMode</td><td>0x1E2</td></tr>'
            "<tr><td>0x1E2</td></tr></table>"
        )
        result = self.parser.parse(html)
        self.assertTrue(result.has_rowspan)
        self.assertEqual(result.rows[0][0], "PEPS_UsageMode")
        self.assertEqual(result.rows[0][1], "0x1E2")
        self.assertEqual(result.rows[1][0], "PEPS_UsageMode")  # expanded
        self.assertEqual(result.rows[1][1], "0x1E2")

    def test_colspan_table(self):
        html = (
            '<table><tr><td colspan="2">Header Span</td></tr>'
            "<tr><td>A</td><td>B</td></tr></table>"
        )
        result = self.parser.parse(html)
        self.assertTrue(result.has_colspan)
        # First row (colspan) is treated as header since no <th>
        # Check that both cols of header row contain "Header Span"
        self.assertIn("Header Span", result.headers[0])
        # Data row should be [A, B]
        self.assertEqual(result.rows[0], ["A", "B"])

    def test_rowspan_and_colspan(self):
        html = (
            '<table><tr><td rowspan="2" colspan="2">Big Cell</td>'
            "<td>C</td></tr>"
            "<tr><td>D</td></tr></table>"
        )
        result = self.parser.parse(html)
        self.assertTrue(result.has_rowspan)
        self.assertTrue(result.has_colspan)
        # Big Cell fills columns 0,1 in both rows
        self.assertEqual(result.headers[0], "Big Cell")
        self.assertEqual(result.headers[1], "Big Cell")
        # Only 1 data row (the second <tr>)
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.rows[0][0], "Big Cell")
        self.assertEqual(result.rows[0][1], "Big Cell")
        self.assertEqual(result.rows[0][2], "D")

    def test_real_mineru_table(self):
        html = (
            '<table><tr>'
            '<td rowspan="2"><p>Front Wiper Mode</p><p>前雨刮模式</p></td>'
            '<td colspan="2"><p>TccSts</p><p>电源模式</p></td>'
            '<td rowspan="2"><p>Front Wiper Output Control</p></td>'
            '</tr><tr>'
            '<td><p>Inactive</p></td>'
            '<td><p>Convenience/driving</p></td>'
            '</tr><tr>'
            '<td><p>OFF</p></td><td>X</td><td>X</td>'
            '<td colspan="2"><p>OFF</p></td>'
            '</tr></table>'
        )
        result = self.parser.parse(html)
        self.assertTrue(result.has_rowspan)
        self.assertTrue(result.has_colspan)
        # Data Row 0: rowspan=2 fills the cell from the header row
        self.assertIn("前雨刮模式", result.rows[0][0])
        # Data Row 1 is a different logical row (OFF), not rowspan fill
        self.assertIn("OFF", result.rows[1][0])

    def test_html_entities_decoded(self):
        html = (
            "<table><tr><th>&amp;&amp;</th><th>test</th></tr>"
            "<tr><td>a</td><td>b</td></tr></table>"
        )
        result = self.parser.parse(html)
        self.assertEqual(result.headers[0], "&&")
        self.assertEqual(result.headers[1], "test")
        self.assertEqual(result.rows[0][0], "a")

    def test_empty_cell(self):
        html = "<table><tr><td></td><td>x</td></tr><tr><td>a</td><td>b</td></tr></table>"
        result = self.parser.parse(html)
        self.assertEqual(result.headers[0], "")
        self.assertEqual(result.headers[1], "x")


if __name__ == "__main__":
    unittest.main()
