from datetime import date
import os
import sqlite3
import tempfile
import unittest

from dux import create_app
from dux.controllers.dashboard_wellness_controller import (
    _build_daily_charts,
    _build_summary,
    _filter_jugadoras_by_plantel,
    _resolve_period_selection,
    _selected_planteles,
)
from dux.models import Base


class DashboardWellnessRouteTest(unittest.TestCase):
    def setUp(self):
        Base.metadata.clear()

        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()

        conn = sqlite3.connect(self.tmp.name)
        conn.execute(
            """
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                password_hash TEXT,
                role_id TEXT,
                state_id INTEGER,
                name TEXT,
                lastname TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO users (id, email, password_hash, state_id, name)
            VALUES ('test-user', 'test@example.com', '', 2, 'Test')
            """
        )
        conn.execute(
            """
            CREATE TABLE futbolistas (
                identificacion TEXT PRIMARY KEY,
                nombre TEXT,
                apellido TEXT,
                competicion TEXT,
                genero TEXT,
                id_estado INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE wellness (
                id INTEGER PRIMARY KEY,
                id_jugadora TEXT,
                fecha_sesion DATE,
                tipo TEXT,
                turno TEXT,
                periodizacion_tactica TEXT,
                recuperacion INTEGER,
                fatiga INTEGER,
                sueno INTEGER,
                stress INTEGER,
                dolor INTEGER,
                id_zona_segmento_dolor INTEGER,
                minutos_sesion INTEGER,
                rpe REAL,
                ua REAL,
                observacion TEXT,
                fecha_hora_registro DATETIME,
                usuario TEXT,
                estatus_id INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE tipo_ausencia (
                id INTEGER PRIMARY KEY,
                nombre TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE ausencias (
                id INTEGER PRIMARY KEY,
                id_jugadora TEXT,
                fecha_inicio DATE,
                fecha_fin DATE,
                motivo_id INTEGER,
                turno TEXT,
                observacion TEXT,
                usuario TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO futbolistas (
                identificacion, nombre, apellido, competicion, genero, id_estado
            )
            VALUES ('player-1', 'Alexia', 'Test', '1FF', 'F', 1)
            """
        )
        conn.execute(
            """
            INSERT INTO futbolistas (
                identificacion, nombre, apellido, competicion, genero, id_estado
            )
            VALUES ('player-2', 'Patri', 'Other', '2FF', 'F', 1)
            """
        )
        conn.execute(
            """
            INSERT INTO tipo_ausencia (id, nombre)
            VALUES (1, 'Lesion')
            """
        )
        today = date.today().isoformat()

        conn.execute(
            """
            INSERT INTO wellness (
                id, id_jugadora, fecha_sesion, tipo, turno, periodizacion_tactica,
                recuperacion, fatiga, sueno, stress, dolor, id_zona_segmento_dolor,
                minutos_sesion, rpe, ua, observacion,
                fecha_hora_registro, usuario, estatus_id
            )
            VALUES (
                1, 'player-1', ?, 'checkOut', 'Turno 1', 'MD+1 / MD-6',
                4, 3, 5, 2, 1, NULL, 60, 5, 300, '', ?, 'test', 2
            )
            """,
            (today, f"{today} 09:00:00"),
        )
        conn.execute(
            """
            INSERT INTO wellness (
                id, id_jugadora, fecha_sesion, tipo, turno, periodizacion_tactica,
                recuperacion, fatiga, sueno, stress, dolor, id_zona_segmento_dolor,
                minutos_sesion, rpe, ua, observacion,
                fecha_hora_registro, usuario, estatus_id
            )
            VALUES (
                2, 'player-2', ?, 'checkOut', 'Turno 1', 'MD+1 / MD-6',
                1, 1, 1, 1, 1, NULL, 60, 4, 240, '', ?, 'test', 2
            )
            """,
            (today, f"{today} 10:00:00"),
        )
        conn.commit()
        conn.close()

        class TestConfig:
            SECRET_KEY = "test-secret"
            TESTING = True
            SQLALCHEMY_DATABASE_URI = f"sqlite:///{self.tmp.name}"
            SQLALCHEMY_TRACK_MODIFICATIONS = False
            WTF_CSRF_ENABLED = False
            CACHE_TYPE = "SimpleCache"

        self.app = create_app(TestConfig)
        self.client = self.app.test_client()

    def tearDown(self):
        Base.metadata.clear()
        os.unlink(self.tmp.name)

    def test_dashboard_wellness_route_renders_for_logged_user(self):
        with self.client.session_transaction() as session:
            session["_user_id"] = "test-user"
            session["_fresh"] = True

        response = self.client.get("/dashboard/wellness/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Wellness", response.data)
        self.assertIn(b"Indicadores de bienestar", response.data)
        self.assertNotIn(b"Solo lectura", response.data)
        self.assertIn(b"Registro Wellness", response.data)
        self.assertIn(b"Test, Alexia", response.data)
        self.assertIn(b"1FF", response.data)
        self.assertIn(b"checkOut", response.data)
        self.assertIn(b"15.0/25", response.data)
        self.assertIn(b"0/1", response.data)
        self.assertIn(b"300.0", response.data)
        self.assertIn(b"Filtros activos", response.data)
        self.assertIn(b"30 d\xc3\xadas", response.data)
        self.assertNotIn(b"Other, Patri", response.data)
        self.assertIn(b'id="chart-wellness"', response.data)
        self.assertIn(b'id="chart-rpe"', response.data)
        self.assertIn(b'id="chart-ua"', response.data)

    def test_dashboard_wellness_registro_shell_renders_for_logged_user(self):
        with self.client.session_transaction() as session:
            session["_user_id"] = "test-user"
            session["_fresh"] = True

        response = self.client.get("/dashboard/wellness/registro/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Registro Wellness", response.data)
        self.assertIn(b"Guardar Check-in", response.data)
        self.assertIn(b"Check-in", response.data)
        self.assertIn(b"Check-out / RPE / UA", response.data)
        self.assertIn(b"Ausencias", response.data)
        self.assertIn(b"Guardar Check-out", response.data)
        self.assertIn(b"Guardar ausencia", response.data)
        self.assertIn(b"Lesion", response.data)
        self.assertIn(b"Test, Alexia", response.data)

    def test_dashboard_wellness_registro_creates_checkin(self):
        with self.client.session_transaction() as session:
            session["_user_id"] = "test-user"
            session["_fresh"] = True

        response = self.client.post(
            "/dashboard/wellness/registro/",
            data={
                "action": "checkin",
                "plantel": "1FF",
                "id_jugadora": "player-1",
                "fecha_sesion": "2026-05-28",
                "turno": "Turno 2",
                "periodizacion_tactica": "MD+1 / MD-6",
                "recuperacion": "1",
                "fatiga": "2",
                "sueno": "3",
                "stress": "2",
                "dolor": "1",
                "observacion": "Test checkin",
            },
        )

        self.assertEqual(response.status_code, 302)

        conn = sqlite3.connect(self.tmp.name)
        row = conn.execute(
            """
            SELECT id_jugadora, fecha_sesion, tipo, turno, recuperacion, fatiga,
                   sueno, stress, dolor, observacion, usuario, estatus_id
            FROM wellness
            WHERE id_jugadora = 'player-1'
              AND fecha_sesion = '2026-05-28'
              AND turno = 'Turno 2'
            """
        ).fetchone()
        conn.close()

        self.assertEqual(
            row,
            (
                "player-1",
                "2026-05-28",
                "checkin",
                "Turno 2",
                1,
                2,
                3,
                2,
                1,
                "Test checkin",
                "Test",
                1,
            ),
        )

    def test_dashboard_wellness_registro_rejects_duplicate_checkin(self):
        with self.client.session_transaction() as session:
            session["_user_id"] = "test-user"
            session["_fresh"] = True

        response = self.client.post(
            "/dashboard/wellness/registro/",
            data={
                "action": "checkin",
                "plantel": "1FF",
                "id_jugadora": "player-1",
                "fecha_sesion": date.today().isoformat(),
                "turno": "Turno 1",
                "periodizacion_tactica": "MD+1 / MD-6",
                "recuperacion": "1",
                "fatiga": "2",
                "sueno": "3",
                "stress": "2",
                "dolor": "1",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Ya existe un Check-in", response.data)

    def test_dashboard_wellness_registro_validates_checkin_fields(self):
        with self.client.session_transaction() as session:
            session["_user_id"] = "test-user"
            session["_fresh"] = True

        response = self.client.post(
            "/dashboard/wellness/registro/",
            data={
                "action": "checkin",
                "plantel": "1FF",
                "id_jugadora": "player-1",
                "fecha_sesion": "2026-05-29",
                "turno": "Turno 1",
                "recuperacion": "0",
                "fatiga": "2",
                "sueno": "3",
                "stress": "2",
                "dolor": "4",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"recuperacion debe estar entre 1 y 5", response.data)
        self.assertIn(b"Selecciona una zona de dolor", response.data)

    def test_dashboard_wellness_registro_updates_checkout(self):
        with self.client.session_transaction() as session:
            session["_user_id"] = "test-user"
            session["_fresh"] = True

        response = self.client.post(
            "/dashboard/wellness/registro/",
            data={
                "action": "checkout",
                "plantel": "1FF",
                "id_jugadora": "player-1",
                "fecha_sesion": date.today().isoformat(),
                "turno": "Turno 1",
                "minutos_sesion": "75",
                "rpe": "6",
            },
        )

        self.assertEqual(response.status_code, 302)

        conn = sqlite3.connect(self.tmp.name)
        row = conn.execute(
            """
            SELECT tipo, minutos_sesion, rpe, ua, usuario, estatus_id
            FROM wellness
            WHERE id = 1
            """
        ).fetchone()
        conn.close()

        self.assertEqual(row, ("checkOut", 75, 6.0, 450.0, "Test", 2))

    def test_dashboard_wellness_registro_rejects_checkout_without_checkin(self):
        with self.client.session_transaction() as session:
            session["_user_id"] = "test-user"
            session["_fresh"] = True

        response = self.client.post(
            "/dashboard/wellness/registro/",
            data={
                "action": "checkout",
                "plantel": "1FF",
                "id_jugadora": "player-1",
                "fecha_sesion": "2026-06-01",
                "turno": "Turno 1",
                "minutos_sesion": "75",
                "rpe": "6",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No existe un Check-in previo", response.data)

    def test_dashboard_wellness_registro_validates_checkout_fields(self):
        with self.client.session_transaction() as session:
            session["_user_id"] = "test-user"
            session["_fresh"] = True

        response = self.client.post(
            "/dashboard/wellness/registro/",
            data={
                "action": "checkout",
                "plantel": "1FF",
                "id_jugadora": "player-1",
                "fecha_sesion": date.today().isoformat(),
                "turno": "Turno 1",
                "minutos_sesion": "0",
                "rpe": "11",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Los minutos de sesi\xc3\xb3n deben ser mayores que 0", response.data)
        self.assertIn(b"El RPE debe estar entre 1 y 10", response.data)

    def test_dashboard_wellness_registro_creates_absence(self):
        with self.client.session_transaction() as session:
            session["_user_id"] = "test-user"
            session["_fresh"] = True

        response = self.client.post(
            "/dashboard/wellness/registro/",
            data={
                "action": "ausencia",
                "plantel": "1FF",
                "id_jugadora": "player-1",
                "fecha_inicio": "2026-05-28",
                "fecha_fin": "2026-05-30",
                "motivo_id": "1",
                "ausencia_turno": "Todos",
                "ausencia_observacion": "Test absence",
            },
        )

        self.assertEqual(response.status_code, 302)

        conn = sqlite3.connect(self.tmp.name)
        row = conn.execute(
            """
            SELECT id_jugadora, fecha_inicio, fecha_fin, motivo_id, turno, observacion, usuario
            FROM ausencias
            WHERE id_jugadora = 'player-1'
            """
        ).fetchone()
        conn.close()

        self.assertEqual(
            row,
            (
                "player-1",
                "2026-05-28",
                "2026-05-30",
                1,
                "Todos",
                "Test absence",
                "Test",
            ),
        )

    def test_dashboard_wellness_registro_validates_absence_fields(self):
        with self.client.session_transaction() as session:
            session["_user_id"] = "test-user"
            session["_fresh"] = True

        response = self.client.post(
            "/dashboard/wellness/registro/",
            data={
                "action": "ausencia",
                "plantel": "1FF",
                "id_jugadora": "player-1",
                "fecha_inicio": "2026-05-30",
                "fecha_fin": "2026-05-28",
                "motivo_id": "",
                "ausencia_turno": "Todos",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"La fecha final no puede ser menor", response.data)
        self.assertIn(b"Selecciona un motivo de ausencia", response.data)

    def test_dashboard_wellness_route_applies_selected_plantel(self):
        with self.client.session_transaction() as session:
            session["_user_id"] = "test-user"
            session["_fresh"] = True

        response = self.client.get("/dashboard/wellness/?plantel=2FF&period=7")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Other, Patri", response.data)
        self.assertIn(b"2FF", response.data)
        self.assertIn(b"5.0/25", response.data)
        self.assertIn(b"7 d\xc3\xadas", response.data)
        self.assertNotIn(b"Test, Alexia", response.data)

    def test_build_summary_uses_legacy_wellness_kpis(self):
        records = [
            {
                "id_jugadora": "player-risk",
                "nombre_jugadora": "Risk Player",
                "tipo": "checkOut",
                "recuperacion": 4,
                "energia": 4,
                "sueno": 4,
                "stress": 4,
                "dolor": 4,
                "wellness_score": 20,
                "rpe": 6,
                "ua": 360,
            },
            {
                "id_jugadora": "player-ok",
                "nombre_jugadora": "Ok Player",
                "tipo": "checkOut",
                "recuperacion": 1,
                "energia": 1,
                "sueno": 1,
                "stress": 1,
                "dolor": 1,
                "wellness_score": 5,
                "rpe": 4,
                "ua": 200,
            },
        ]

        summary = _build_summary(records)

        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["wellness_promedio"], 12.5)
        self.assertEqual(summary["wellness_estado"], "en fatiga")
        self.assertEqual(summary["rpe_promedio"], 5.0)
        self.assertEqual(summary["rpe_estado"], "moderado")
        self.assertEqual(summary["ua_total"], 560)
        self.assertEqual(summary["alertas_count"], 1)
        self.assertEqual(summary["alertas_total_jugadoras"], 2)
        self.assertEqual(summary["alertas_pct"], 50.0)

    def test_build_daily_charts_aggregates_records_by_date(self):
        records = [
            {
                "fecha_sesion": date(2026, 5, 20),
                "wellness_score": 10,
                "rpe": 4,
                "ua": 200,
            },
            {
                "fecha_sesion": date(2026, 5, 20),
                "wellness_score": 20,
                "rpe": 6,
                "ua": 360,
            },
            {
                "fecha_sesion": date(2026, 5, 21),
                "wellness_score": 15,
                "rpe": 5,
                "ua": 300,
            },
        ]

        charts = _build_daily_charts(records)

        self.assertEqual(charts["labels"], ["2026-05-20", "2026-05-21"])
        self.assertEqual(charts["wellness"], [15.0, 15.0])
        self.assertEqual(charts["rpe"], [5.0, 5.0])
        self.assertEqual(charts["ua"], [560, 300])

    def test_filter_helpers_resolve_defaults_and_visible_players(self):
        self.assertEqual(_resolve_period_selection(None), ("30", 30))
        self.assertEqual(_resolve_period_selection("90"), ("90", 90))
        self.assertEqual(_resolve_period_selection("unknown", "365"), ("365", 365))
        self.assertEqual(_resolve_period_selection("unknown", "999"), ("365", 365))

        self.assertEqual(_selected_planteles([], ["2FF", "1FF"]), ["1FF"])
        self.assertEqual(_selected_planteles(["2FF"], ["2FF", "1FF"]), ["2FF"])
        self.assertEqual(_selected_planteles(["invalid"], ["2FF"]), [])

        jugadoras = [
            {"id": "player-1", "plantel": "1FF"},
            {"id": "player-2", "plantel": "2FF"},
        ]
        self.assertEqual(
            _filter_jugadoras_by_plantel(jugadoras, ["1FF"]),
            [{"id": "player-1", "plantel": "1FF"}],
        )


if __name__ == "__main__":
    unittest.main()
