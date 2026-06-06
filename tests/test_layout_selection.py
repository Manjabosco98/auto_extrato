import unittest

from src.schemas.base import BankHandler, layout


class FakeSicoob(BankHandler):
    bank = "Fake Sicoob"

    @layout("SICOOB")
    def layout1(self, pdf):
        return "layout1"

    @layout(
        "PLATAFORMA DE SERVIÇOS FINANCEIROS DO SICOOB – SISBR",
        " EXTRATO DE CARTÃO DE CRÉDITO ",
    )
    def layout3(self, pdf):
        return "layout3"

    @layout("EMPATE A")
    def tie1(self, pdf):
        return "tie1"

    @layout("EMPATE B")
    def tie2(self, pdf):
        return "tie2"


class LayoutSelectionTest(unittest.TestCase):
    def setUp(self):
        self.handler = FakeSicoob()

    def test_picks_layout_with_more_matching_signatures(self):
        pdf = [
            "SICOOB",
            "PLATAFORMA DE SERVIÇOS FINANCEIROS DO SICOOB – SISBR",
            "01/06/2026 EXTRATO DE CARTÃO DE CRÉDITO 07:40:53",
        ]

        self.assertEqual(self.handler._pick_layout(pdf).__name__, "layout3")

    def test_picks_single_signature_layout_when_only_it_matches(self):
        self.assertEqual(self.handler._pick_layout(["SICOOB"]).__name__, "layout1")

    def test_keeps_definition_order_when_specificity_ties(self):
        self.assertEqual(
            self.handler._pick_layout(["EMPATE A", "EMPATE B"]).__name__,
            "tie1",
        )

    def test_returns_none_when_no_layout_matches(self):
        self.assertIsNone(self.handler._pick_layout(["BANCO DESCONHECIDO"]))


if __name__ == "__main__":
    unittest.main()
