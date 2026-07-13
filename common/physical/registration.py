from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping

from sqlalchemy import text

from dux import db


BASIC_FIELDS = [
    "fecha_medicion",
    "metodo",
    "peso_bruto_kg",
    "talla_corporal_cm",
    "talla_sentado_cm",
    "envergadura_cm",
]

SKINFOLD_FIELDS = [
    "pliegue_triceps",
    "pliegue_subescapular",
    "pliegue_biceps",
    "pliegue_cresta_iliaca",
    "pliegue_supraespinal",
    "pliegue_abdominal",
    "pliegue_muslo_frontal",
    "pliegue_pantorrilla_maxima",
    "pliegue_antebrazo",
]

PERIMETER_FIELDS = [
    "perimetro_cabeza",
    "perimetro_cuello",
    "perimetro_brazo_relajado",
    "perimetro_brazo_flexionado_en_tension",
    "perimetro_antebrazo_maximo",
    "perimetro_muneca",
    "perimetro_torax_mesoesternal",
    "perimetro_cintura_minima",
    "perimetro_abdominal_maxima",
    "perimetro_cadera_maximo",
    "perimetro_muslo_maximo",
    "perimetro_muslo_medial",
    "perimetro_pantorrilla_maxima",
    "perimetro_tobillo_minima",
]

DIAMETER_FIELDS = [
    "biacromial",
    "torax_transverso",
    "torax_antero_posterior",
    "bi_iliocrestideo",
    "humeral_biepicondilar",
    "femoral_biepicondilar",
    "muneca_biestiloideo",
    "tobillo_bimaleolar",
    "mano",
]

LENGTH_FIELDS = [
    "acromial_radial",
    "radial_estiloidea",
    "medial_estiloidea_dactilar",
    "ilioespinal",
    "trocanterea",
    "troc_tibial_lateral",
    "tibial_lateral",
    "tibial_medial_maleolar_medial",
    "pie",
]

OBSERVATION_FIELDS = ["observaciones"]

NUMERIC_FIELDS = (
    ["peso_bruto_kg", "talla_corporal_cm", "talla_sentado_cm", "envergadura_cm"]
    + SKINFOLD_FIELDS
    + PERIMETER_FIELDS
    + DIAMETER_FIELDS
    + LENGTH_FIELDS
)

REGISTRATION_FIELDS = (
    ["id_jugadora", "tipo_isak", "_modo"]
    + BASIC_FIELDS
    + SKINFOLD_FIELDS
    + PERIMETER_FIELDS
    + DIAMETER_FIELDS
    + LENGTH_FIELDS
    + OBSERVATION_FIELDS
)

REPETIBLE_FIELDS = (
    ["peso_bruto_kg"]
    + SKINFOLD_FIELDS
    + PERIMETER_FIELDS
)

STRUCTURAL_FIELDS = [
    "talla_corporal_cm",
    "talla_sentado_cm",
    "envergadura_cm",
] + DIAMETER_FIELDS + LENGTH_FIELDS

CRITICAL_SKINFOLDS = [
    "pliegue_triceps",
    "pliegue_subescapular",
    "pliegue_supraespinal",
    "pliegue_abdominal",
    "pliegue_muslo_frontal",
    "pliegue_pantorrilla_maxima",
]

INSERT_TABLE_ORDER = [
    "antropometria_isak",
    "antropometria_isak_basicos",
    "antropometria_isak_longitudes",
    "antropometria_isak_diametros",
    "antropometria_isak_perimetros",
    "antropometria_isak_pliegues",
]


def _user_label(current_user) -> str | None:
    for attr in ("name", "nombre", "email", "id"):
        value = getattr(current_user, attr, None)
        if value:
            return str(value).strip().lower()
    return None


def build_registration_record_from_form(form: Mapping[str, Any], current_user) -> dict[str, Any]:
    record = {field: form.get(field) for field in REGISTRATION_FIELDS}
    record["id_jugadora"] = form.get("id_jugadora") or form.get("jugadora")
    record["fecha_medicion"] = record.get("fecha_medicion") or date.today().isoformat()
    record["_modo"] = (form.get("_modo") or "COMPLETO").upper()
    record["tipo_isak"] = "COMPLETO"
    record["metodo"] = "ISAK"
    record["usuario"] = _user_label(current_user)
    record["estatus_id"] = 1
    return record


def _parse_float(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nan", "-"}:
        return None
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return value


def _parse_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return text


def normalize_registration_record(record: dict[str, Any]) -> dict[str, Any]:
    normalized = {}
    for field, value in record.items():
        if field == "fecha_medicion":
            normalized[field] = _parse_date(value)
        elif field in NUMERIC_FIELDS:
            parsed = _parse_float(value)
            normalized[field] = round(parsed, 2) if isinstance(parsed, float) else parsed
        elif value == "None":
            normalized[field] = None
        else:
            normalized[field] = value

    normalized["_modo"] = str(normalized.get("_modo") or "").upper()
    normalized["tipo_isak"] = "COMPLETO"
    normalized["metodo"] = "ISAK"
    normalized["estatus_id"] = int(normalized.get("estatus_id") or 1)
    return normalized


def _is_positive_number(value) -> bool:
    return isinstance(value, (int, float)) and value > 0


def validate_registration_record(record: dict[str, Any]) -> tuple[bool, list[str]]:
    errors = []

    for field in ("id_jugadora", "fecha_medicion", "tipo_isak", "usuario"):
        if not record.get(field):
            errors.append(f"Falta campo obligatorio: {field}")

    try:
        datetime.fromisoformat(str(record.get("fecha_medicion")))
    except (TypeError, ValueError):
        errors.append("fecha_medicion invalida")

    modo = record.get("_modo")
    if modo not in {"COMPLETO", "SEGUIMIENTO"}:
        errors.append("Modo ISAK invalido")

    record["tipo_isak"] = "COMPLETO"

    required_fields = list(REPETIBLE_FIELDS)
    if modo == "COMPLETO":
        required_fields += STRUCTURAL_FIELDS

    missing = [
        field
        for field in required_fields
        if not _is_positive_number(record.get(field))
    ]
    if missing:
        errors.append("Faltan o son invalidos los siguientes campos ISAK: " + ", ".join(missing))

    if not _is_positive_number(record.get("talla_corporal_cm")):
        errors.append("La talla debe ser mayor que 0 para los calculos")

    if not _is_positive_number(record.get("peso_bruto_kg")):
        errors.append("El peso debe ser mayor que 0 para los calculos")

    invalid_skinfolds = [
        field
        for field in CRITICAL_SKINFOLDS
        if not _is_positive_number(record.get(field))
    ]
    if invalid_skinfolds:
        errors.append("Pliegues necesarios para los calculos no validos: " + ", ".join(invalid_skinfolds))

    return not errors, errors


def split_registration_payload(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        "antropometria_isak": {
            "id_jugadora": record.get("id_jugadora"),
            "fecha_medicion": record.get("fecha_medicion"),
            "tipo_isak": record.get("tipo_isak"),
            "observaciones": record.get("observaciones"),
            "usuario": record.get("usuario"),
            "estatus_id": record.get("estatus_id", 1),
        },
        "antropometria_isak_basicos": {
            field: record.get(field)
            for field in ["peso_bruto_kg", "talla_corporal_cm", "talla_sentado_cm", "envergadura_cm"]
        },
        "antropometria_isak_longitudes": {
            field: record.get(field)
            for field in LENGTH_FIELDS
        },
        "antropometria_isak_diametros": {
            field: record.get(field)
            for field in DIAMETER_FIELDS
        },
        "antropometria_isak_perimetros": {
            field: record.get(field)
            for field in PERIMETER_FIELDS
        },
        "antropometria_isak_pliegues": {
            field: record.get(field)
            for field in SKINFOLD_FIELDS
        },
    }


def _execute_insert(sql: str, params: dict[str, Any]):
    return db.session.execute(text(sql), params)


def _last_insert_id(result) -> int:
    lastrowid = getattr(result, "lastrowid", None)
    if lastrowid:
        return int(lastrowid)
    row = db.session.execute(text("SELECT LAST_INSERT_ID() AS id_isak")).mappings().first()
    return int(row["id_isak"])


def save_registration_record(record: dict[str, Any]) -> int:
    payload = split_registration_payload(record)

    try:
        with db.session.begin():
            result = _execute_insert(
                """
                INSERT INTO antropometria_isak (
                    id_jugadora,
                    fecha_medicion,
                    tipo_isak,
                    observaciones,
                    usuario,
                    estatus_id
                ) VALUES (
                    :id_jugadora,
                    :fecha_medicion,
                    :tipo_isak,
                    :observaciones,
                    :usuario,
                    :estatus_id
                );
                """,
                payload["antropometria_isak"],
            )
            id_isak = _last_insert_id(result)

            basicos = {"id_isak": id_isak, **payload["antropometria_isak_basicos"]}
            _execute_insert(
                """
                INSERT INTO antropometria_isak_basicos (
                    id_isak,
                    peso_bruto_kg,
                    talla_corporal_cm,
                    talla_sentado_cm,
                    envergadura_cm
                ) VALUES (
                    :id_isak,
                    :peso_bruto_kg,
                    :talla_corporal_cm,
                    :talla_sentado_cm,
                    :envergadura_cm
                );
                """,
                basicos,
            )

            longitudes = {"id_isak": id_isak, **payload["antropometria_isak_longitudes"]}
            _execute_insert(
                """
                INSERT INTO antropometria_isak_longitudes (
                    id_isak,
                    len_acromial_radial_cm,
                    len_radial_estiloidea_cm,
                    len_medial_estiloidea_dactilar_cm,
                    len_ilioespinal_cm,
                    len_trocanterea_cm,
                    len_troc_tibial_lateral_cm,
                    len_tibial_lateral_cm,
                    len_tibial_medial_maleolar_medial_cm,
                    len_pie_cm
                ) VALUES (
                    :id_isak,
                    :acromial_radial,
                    :radial_estiloidea,
                    :medial_estiloidea_dactilar,
                    :ilioespinal,
                    :trocanterea,
                    :troc_tibial_lateral,
                    :tibial_lateral,
                    :tibial_medial_maleolar_medial,
                    :pie
                );
                """,
                longitudes,
            )

            diametros = {"id_isak": id_isak, **payload["antropometria_isak_diametros"]}
            _execute_insert(
                """
                INSERT INTO antropometria_isak_diametros (
                    id_isak,
                    diam_biacromial_cm,
                    diam_torax_transverso_cm,
                    diam_torax_anteroposterior_cm,
                    diam_biiliocrestideo_cm,
                    diam_humeral_biepicondilar_cm,
                    diam_femoral_biepicondilar_cm,
                    diam_muneca_biestiloideo_cm,
                    diam_tobillo_bimaleolar_cm,
                    diam_mano_cm
                ) VALUES (
                    :id_isak,
                    :biacromial,
                    :torax_transverso,
                    :torax_antero_posterior,
                    :bi_iliocrestideo,
                    :humeral_biepicondilar,
                    :femoral_biepicondilar,
                    :muneca_biestiloideo,
                    :tobillo_bimaleolar,
                    :mano
                );
                """,
                diametros,
            )

            perimetros = {"id_isak": id_isak, **payload["antropometria_isak_perimetros"]}
            _execute_insert(
                """
                INSERT INTO antropometria_isak_perimetros (
                    id_isak,
                    per_cabeza_cm,
                    per_cuello_cm,
                    per_brazo_relajado_cm,
                    per_brazo_flexionado_tension_cm,
                    per_antebrazo_maximo_cm,
                    per_muneca_cm,
                    per_torax_mesoesternal_cm,
                    per_cintura_minima_cm,
                    per_abdominal_maxima_cm,
                    per_cadera_maxima_cm,
                    per_muslo_maximo_cm,
                    per_muslo_medial_cm,
                    per_pantorrilla_maxima_cm,
                    per_tobillo_minima_cm
                ) VALUES (
                    :id_isak,
                    :perimetro_cabeza,
                    :perimetro_cuello,
                    :perimetro_brazo_relajado,
                    :perimetro_brazo_flexionado_en_tension,
                    :perimetro_antebrazo_maximo,
                    :perimetro_muneca,
                    :perimetro_torax_mesoesternal,
                    :perimetro_cintura_minima,
                    :perimetro_abdominal_maxima,
                    :perimetro_cadera_maximo,
                    :perimetro_muslo_maximo,
                    :perimetro_muslo_medial,
                    :perimetro_pantorrilla_maxima,
                    :perimetro_tobillo_minima
                );
                """,
                perimetros,
            )

            pliegues = {"id_isak": id_isak, **payload["antropometria_isak_pliegues"]}
            _execute_insert(
                """
                INSERT INTO antropometria_isak_pliegues (
                    id_isak,
                    pl_triceps_mm,
                    pl_subescapular_mm,
                    pl_biceps_mm,
                    pl_cresta_iliaca_mm,
                    pl_supraespinal_mm,
                    pl_abdominal_mm,
                    pl_muslo_frontal_mm,
                    pl_pantorrilla_maxima_mm,
                    pl_antebrazo_mm
                ) VALUES (
                    :id_isak,
                    :pliegue_triceps,
                    :pliegue_subescapular,
                    :pliegue_biceps,
                    :pliegue_cresta_iliaca,
                    :pliegue_supraespinal,
                    :pliegue_abdominal,
                    :pliegue_muslo_frontal,
                    :pliegue_pantorrilla_maxima,
                    :pliegue_antebrazo
                );
                """,
                pliegues,
            )

            return id_isak
    except Exception:
        db.session.rollback()
        raise
