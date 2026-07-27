import os
import tempfile
import unittest
from unittest import mock

from presentation.controllers import funcionalidad


class TestHistoryExport(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def path(self, name):
        return os.path.join(self.temp_dir.name, name)

    def test_export_summary_as_text(self):
        destination = self.path("resumen.txt")

        with (
            mock.patch.object(
                funcionalidad.filedialog,
                "asksaveasfilename",
                return_value=destination,
            ),
            mock.patch.object(funcionalidad, "show_info"),
            mock.patch.object(funcionalidad, "_abrir_archivo_exportado"),
        ):
            result = funcionalidad.exportar_resumen("Punto uno\nPunto dos")

        self.assertEqual(result, destination)
        with open(destination, encoding="utf-8") as exported:
            self.assertEqual(exported.read(), "Punto uno\nPunto dos")

    def test_cancel_summary_export_writes_nothing(self):
        with (
            mock.patch.object(
                funcionalidad.filedialog,
                "asksaveasfilename",
                return_value="",
            ),
            mock.patch.object(funcionalidad, "show_info") as show_info,
        ):
            result = funcionalidad.exportar_resumen("Resumen disponible")

        self.assertIsNone(result)
        show_info.assert_not_called()
        self.assertEqual(os.listdir(self.temp_dir.name), [])

    def test_audio_export_copies_bytes_and_preserves_source_extension(self):
        source = self.path("entrevista.mp3")
        with open(source, "wb") as audio:
            audio.write(b"audio sin recodificar")
        selected_destination = self.path("copia.wav")
        expected_destination = self.path("copia.mp3")

        with (
            mock.patch.object(
                funcionalidad.filedialog,
                "asksaveasfilename",
                return_value=selected_destination,
            ),
            mock.patch.object(funcionalidad, "show_info"),
            mock.patch.object(funcionalidad, "_abrir_archivo_exportado"),
        ):
            result = funcionalidad.exportar_audio(source)

        self.assertEqual(result, expected_destination)
        with open(expected_destination, "rb") as exported:
            self.assertEqual(exported.read(), b"audio sin recodificar")
        self.assertFalse(os.path.exists(selected_destination))

    def test_audio_is_only_available_for_existing_supported_files(self):
        supported = self.path("grabacion.opus")
        unsupported = self.path("notas.txt")
        with open(supported, "wb") as output:
            output.write(b"opus")
        with open(unsupported, "w", encoding="utf-8") as output:
            output.write("texto")

        self.assertTrue(funcionalidad.es_archivo_multimedia_exportable(supported))
        self.assertFalse(funcionalidad.es_archivo_multimedia_exportable(unsupported))
        self.assertFalse(
            funcionalidad.es_archivo_multimedia_exportable(self.path("falta.mp3"))
        )


if __name__ == "__main__":
    unittest.main()
