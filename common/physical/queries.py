from __future__ import annotations

from typing import Any

from sqlalchemy import text

from dux import db


def _fetch_all(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Ejecuta una consulta SELECT y devuelve una lista de diccionarios."""
    rows = db.session.execute(text(sql), params or {}).mappings().all()
    return [dict(row) for row in rows]


def _fetch_one(sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Ejecuta una consulta SELECT y devuelve un unico diccionario."""
    row = db.session.execute(text(sql), params or {}).mappings().first()
    return dict(row) if row else None


def get_physical_competitions() -> list[dict[str, Any]]:
    """
    Devuelve los planteles disponibles para Physical.
    """
    sql = """
        SELECT DISTINCT
            f.competicion AS codigo,
            COALESCE(d.nombre_competicion, f.competicion) AS nombre
        FROM futbolistas f
        LEFT JOIN diccionario_competiciones d
            ON f.competicion = d.id
        WHERE f.competicion IS NOT NULL
          AND f.competicion <> ''
          AND f.genero = 'F'
          AND f.id_estado = 1
        ORDER BY nombre ASC;
    """
    return _fetch_all(sql)


def get_physical_players(plantel: str | None = None) -> list[dict[str, Any]]:
    """
    Carga jugadoras activas desde futbolistas + informacion_futbolistas.
    """
    where = [
        "f.genero = 'F'",
        "f.id_estado = 1",
    ]
    params: dict[str, Any] = {}

    if plantel:
        where.append("f.competicion = :plantel")
        params["plantel"] = plantel

    sql = f"""
        SELECT
            f.id,
            f.identificacion,
            CONCAT(TRIM(f.nombre), ' ', TRIM(f.apellido)) AS nombre_jugadora,
            f.nombre,
            f.apellido,
            f.competicion AS plantel,
            f.fecha_nacimiento,
            f.genero,
            i.posicion,
            i.dorsal,
            i.nacionalidad,
            i.altura,
            i.peso,
            i.foto_url,
            i.foto_url_drive
        FROM futbolistas f
        LEFT JOIN informacion_futbolistas i
            ON f.identificacion = i.identificacion
        WHERE {" AND ".join(where)}
        ORDER BY f.nombre ASC, f.apellido ASC;
    """

    return _fetch_all(sql, params)


def get_physical_records(
    plantel: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """
    Devuelve sesiones ISAK por jugadora.
    """
    where = [
        "f.genero = 'F'",
        "f.id_estado = 1",
        "i.estatus_id IN (1, 2)",
    ]
    params: dict[str, Any] = {}

    if plantel:
        where.append("f.competicion = :plantel")
        params["plantel"] = plantel

    limit_sql = ""
    if limit is not None:
        limit_sql = " LIMIT :limit"
        params["limit"] = int(limit)

    sql = f"""
        SELECT
            i.id_isak,
            i.id_jugadora AS identificacion,
            CONCAT(TRIM(f.nombre), ' ', TRIM(f.apellido)) AS nombre_jugadora,
            i.tipo_isak,
            i.fecha_medicion,
            f.competicion AS plantel,
            i.usuario,
            i.created_at
        FROM antropometria_isak i
        INNER JOIN futbolistas f
            ON i.id_jugadora = f.identificacion
        WHERE {" AND ".join(where)}
        ORDER BY i.fecha_medicion DESC, i.created_at DESC
        {limit_sql};
    """

    return _fetch_all(sql, params)


def get_physical_overview_stats(plantel: str | None = None) -> dict[str, Any]:
    """
    KPIs base del modulo Physical.
    """
    player_filter = [
        "genero = 'F'",
        "id_estado = 1",
    ]

    records_filter = [
        "f.genero = 'F'",
        "f.id_estado = 1",
        "i.estatus_id IN (1, 2)",
    ]

    params: dict[str, Any] = {}

    if plantel:
        player_filter.append("competicion = :plantel")
        records_filter.append("f.competicion = :plantel")
        params["plantel"] = plantel

    players_sql = f"""
        SELECT COUNT(*) AS total
        FROM futbolistas
        WHERE {" AND ".join(player_filter)};
    """

    records_sql = f"""
        SELECT
            COUNT(*) AS total_registros,
            COUNT(DISTINCT i.id_jugadora) AS jugadoras_con_registro,
            MAX(i.fecha_medicion) AS ultima_medicion
        FROM antropometria_isak i
        INNER JOIN futbolistas f
            ON i.id_jugadora = f.identificacion
        WHERE {" AND ".join(records_filter)};
    """

    players_row = _fetch_one(players_sql, params) or {}
    records_row = _fetch_one(records_sql, params) or {}

    return {
        "total_jugadoras": players_row.get("total", 0) or 0,
        "total_registros": records_row.get("total_registros", 0) or 0,
        "jugadoras_con_registro": records_row.get("jugadoras_con_registro", 0) or 0,
        "ultima_medicion": records_row.get("ultima_medicion"),
    }


def get_physical_full_records(plantel: str | None = None) -> list[dict[str, Any]]:
    """
    Devuelve registros ISAK enriquecidos con mediciones RAW.

    Los calculados antropometricos se generan en memoria desde services.py.
    """
    where = [
        "f.genero = 'F'",
        "f.id_estado = 1",
        "i.estatus_id IN (1, 2)",
    ]
    params: dict[str, Any] = {}

    if plantel:
        where.append("f.competicion = :plantel")
        params["plantel"] = plantel

    sql = f"""
        SELECT
            i.id_isak,
            i.id_jugadora AS identificacion,
            CONCAT(TRIM(f.nombre), ' ', TRIM(f.apellido)) AS nombre_jugadora,
            f.competicion AS plantel,
            i.tipo_isak,
            i.fecha_medicion,
            i.usuario,
            i.created_at,

            b.peso_bruto_kg,
            b.talla_corporal_cm,
            b.talla_sentado_cm,
            b.envergadura_cm,

            p.pl_triceps_mm AS pliegue_triceps,
            p.pl_subescapular_mm AS pliegue_subescapular,
            p.pl_biceps_mm AS pliegue_biceps,
            p.pl_cresta_iliaca_mm AS pliegue_cresta_iliaca,
            p.pl_supraespinal_mm AS pliegue_supraespinal,
            p.pl_abdominal_mm AS pliegue_abdominal,
            p.pl_muslo_frontal_mm AS pliegue_muslo_frontal,
            p.pl_pantorrilla_maxima_mm AS pliegue_pantorrilla_maxima,
            p.pl_antebrazo_mm AS pliegue_antebrazo,

            per.per_cabeza_cm AS perimetro_cabeza,
            per.per_cuello_cm AS perimetro_cuello,
            per.per_brazo_relajado_cm AS perimetro_brazo_relajado,
            per.per_brazo_flexionado_tension_cm AS perimetro_brazo_flexionado_en_tension,
            per.per_antebrazo_maximo_cm AS perimetro_antebrazo_maximo,
            per.per_muneca_cm AS perimetro_muneca,
            per.per_torax_mesoesternal_cm AS perimetro_torax_mesoesternal,
            per.per_cintura_minima_cm AS perimetro_cintura_minima,
            per.per_abdominal_maxima_cm AS perimetro_abdominal_maxima,
            per.per_cadera_maxima_cm AS perimetro_cadera_maximo,
            per.per_muslo_maximo_cm AS perimetro_muslo_maximo,
            per.per_muslo_medial_cm AS perimetro_muslo_medial,
            per.per_pantorrilla_maxima_cm AS perimetro_pantorrilla_maxima,
            per.per_tobillo_minima_cm AS perimetro_tobillo_minima,

            d.diam_biacromial_cm AS biacromial,
            d.diam_torax_transverso_cm AS torax_transverso,
            d.diam_torax_anteroposterior_cm AS torax_antero_posterior,
            d.diam_biiliocrestideo_cm AS bi_iliocrestideo,
            d.diam_humeral_biepicondilar_cm AS humeral_biepicondilar,
            d.diam_femoral_biepicondilar_cm AS femoral_biepicondilar,
            d.diam_muneca_biestiloideo_cm AS muneca_biestiloideo,
            d.diam_tobillo_bimaleolar_cm AS tobillo_bimaleolar,
            d.diam_mano_cm AS mano,

            l.len_acromial_radial_cm AS acromial_radial,
            l.len_radial_estiloidea_cm AS radial_estiloidea,
            l.len_medial_estiloidea_dactilar_cm AS medial_estiloidea_dactilar,
            l.len_ilioespinal_cm AS ilioespinal,
            l.len_trocanterea_cm AS trocanterea,
            l.len_troc_tibial_lateral_cm AS troc_tibial_lateral,
            l.len_tibial_lateral_cm AS tibial_lateral,
            l.len_tibial_medial_maleolar_medial_cm AS tibial_medial_maleolar_medial,
            l.len_pie_cm AS pie

        FROM antropometria_isak i
        INNER JOIN futbolistas f
            ON i.id_jugadora = f.identificacion

        LEFT JOIN antropometria_isak_basicos b
            ON i.id_isak = b.id_isak

        LEFT JOIN antropometria_isak_pliegues p
            ON i.id_isak = p.id_isak

        LEFT JOIN antropometria_isak_perimetros per
            ON i.id_isak = per.id_isak

        LEFT JOIN antropometria_isak_diametros d
            ON i.id_isak = d.id_isak

        LEFT JOIN antropometria_isak_longitudes l
            ON i.id_isak = l.id_isak

        WHERE {" AND ".join(where)}
        ORDER BY i.fecha_medicion DESC, i.created_at DESC;
    """

    return _fetch_all(sql, params)
