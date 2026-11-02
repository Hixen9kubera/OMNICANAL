"""
El estado con que se HIDRATA una variante tiene que ser el mismo con que la
cuenta el índice (`wp_db._estado_efectivo`). Si no, el Publicador dice «87» y
pinta cero: el filtro de la vista tira las filas que el índice sí contó.

Desde el 2-nov-2026 el Publicador (vista «productos») también lista draft e
inprogress (Eduardo: el estado draft se salta), así que ahí la marca ya no
decide: una variante de un padre en borrador entra con marca o sin ella. Sigue
decidiendo en Crear, que solo ve lo NO procesado.

    cd backend && python -m unittest tests.test_estado_variante -v
"""
import unittest

from services import wp_db


class EstadoVariante(unittest.TestCase):
    def test_procesada_con_padre_en_borrador_vale_pending(self):
        for padre in ("draft", "inprogress"):
            self.assertEqual(wp_db._estado_variante(padre, "2026-09-29 17:47:51"), "pending")

    def test_sin_marca_hereda_el_del_padre(self):
        self.assertEqual(wp_db._estado_variante("draft", None), "draft")

    def test_marca_vacia_cuenta_como_marca_igual_que_el_sql(self):
        # El índice pregunta `IS NOT NULL`: '' también es «procesada».
        self.assertEqual(wp_db._estado_variante("draft", ""), "pending")

    def test_padre_ya_resuelto_no_cambia(self):
        for padre in ("publish", "pending", "ready", "private"):
            self.assertEqual(wp_db._estado_variante(padre, "2026-09-29"), padre)

    def test_pending_entra_a_productos_y_sale_de_crear(self):
        e = wp_db._estado_variante("draft", "x")
        self.assertIn(e, wp_db._VISTAS_SQL["productos"])
        self.assertNotIn(e, wp_db._VISTAS_SQL["crear"])

    def test_sin_marca_tambien_entra_al_publicador_y_sigue_en_crear(self):
        # La regla vieja era «sin marca = solo Crear». Desde el 2-nov-2026 el
        # borrador es del Publicador; Crear lo sigue viendo mientras exista.
        for padre in ("draft", "inprogress"):
            e = wp_db._estado_variante(padre, None)
            self.assertIn(e, wp_db._VISTAS_SQL["productos"])
            self.assertIn(e, wp_db._VISTAS_SQL["crear"])


if __name__ == "__main__":
    unittest.main()
