import unittest

from infrastructure.services.temario_index import (
    Section,
    select_context,
    split_sections,
    tokenize,
)

TEMARIO = """# Programación Orientada a Objetos

## Unidad 1: Encapsulamiento
El encapsulamiento oculta el estado interno de un objeto y expone solo
operaciones. Los atributos privados se acceden mediante getters y setters.

## Unidad 2: Herencia
La herencia permite que una clase derive de otra y reutilice su
comportamiento. La clase hija extiende a la clase padre.

## Unidad 3: Polimorfismo
El polimorfismo permite que distintas clases respondan al mismo mensaje
con implementaciones diferentes. El despacho dinamico resuelve el metodo
en tiempo de ejecucion.
"""


class TokenizeTests(unittest.TestCase):
    def test_strips_accents_and_case(self):
        self.assertIn("funcion", tokenize("Función"))

    def test_drops_stopwords_and_short_tokens(self):
        tokens = tokenize("el de la herencia y un")
        self.assertEqual(tokens, ["herencia"])


class SplitSectionsTests(unittest.TestCase):
    def test_splits_on_markdown_headings(self):
        sections = split_sections(TEMARIO)
        headings = [s.heading for s in sections]
        self.assertIn(
            "Programación Orientada a Objetos > Unidad 2: Herencia", headings
        )

    def test_heading_breadcrumb_resets_on_shallower_level(self):
        md = "# A\ntexto a\n## A1\ntexto a1\n# B\ntexto b"
        headings = [s.heading for s in split_sections(md)]
        self.assertEqual(headings, ["A", "A > A1", "B"])

    def test_plain_text_falls_back_to_paragraphs(self):
        sections = split_sections("primer parrafo\n\nsegundo parrafo")
        self.assertEqual(len(sections), 2)
        self.assertEqual([s.heading for s in sections], ["", ""])

    def test_empty_context(self):
        self.assertEqual(split_sections("   "), [])


class SelectContextTests(unittest.TestCase):
    def test_short_context_returned_untouched(self):
        self.assertEqual(select_context("temario corto", "pregunta", 500), "temario corto")

    def test_retrieves_the_asked_unit_and_drops_the_others(self):
        selected = select_context(TEMARIO, "explique la herencia entre clases", 220)
        self.assertIn("herencia", selected.lower())
        self.assertNotIn("polimorfismo", selected.lower())

    def test_retrieved_section_keeps_its_heading(self):
        selected = select_context(TEMARIO, "que es el polimorfismo", 260)
        self.assertIn("Unidad 3", selected)

    def test_respects_the_char_budget(self):
        selected = select_context(TEMARIO, "encapsulamiento herencia polimorfismo", 300)
        self.assertLessEqual(len(selected), 300)

    def test_unmatched_query_falls_back_to_truncation(self):
        selected = select_context(TEMARIO, "zzz cocina italiana", 200)
        self.assertTrue(selected.startswith("# Programación"))
        self.assertLessEqual(len(selected), 210)

    def test_empty_query_falls_back_to_truncation(self):
        selected = select_context(TEMARIO, "", 200)
        self.assertTrue(selected.startswith("# Programación"))

    def test_oversized_single_section_is_truncated(self):
        huge = "## Tema\n" + "herencia " * 500
        selected = select_context(huge, "herencia", 100)
        self.assertLessEqual(len(selected), 110)
        self.assertIn("herencia", selected)

    def test_sections_come_back_in_document_order(self):
        selected = select_context(TEMARIO, "encapsulamiento y polimorfismo", 700)
        self.assertLess(
            selected.index("Encapsulamiento"), selected.index("Polimorfismo")
        )


class SectionTests(unittest.TestCase):
    def test_render_without_heading(self):
        self.assertEqual(Section(heading="", body="cuerpo").render(), "cuerpo")


if __name__ == "__main__":
    unittest.main()
