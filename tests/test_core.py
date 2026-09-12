import unittest

from strictlyboolean_core import parse_query


class ParserTests(unittest.TestCase):
    def test_implicit_and(self):
        self.assertEqual(
            parse_query("DJD Dividend ETF").describe(),
            "((DJD AND Dividend) AND ETF)",
        )

    def test_term_and_phrase_implicit_and(self):
        self.assertEqual(
            parse_query('DJD "Dividend ETF"').describe(),
            '(DJD AND "Dividend ETF")',
        )

    def test_not_shorthand(self):
        self.assertEqual(
            parse_query("DJD NOT Schwab").describe(),
            "(DJD AND NOT (Schwab))",
        )

    def test_operator_precedence(self):
        self.assertEqual(
            parse_query("Merrick OR Rusty California").describe(),
            "(Merrick OR (Rusty AND California))",
        )

    def test_parentheses(self):
        self.assertEqual(
            parse_query("California (Merrick OR Rusty)").describe(),
            "(California AND (Merrick OR Rusty))",
        )


if __name__ == "__main__":
    unittest.main()
