from __future__ import annotations

from typing import Any

from sqlalchemy import text

from dux import db


def _fetch_all(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    rows = db.session.execute(text(sql), params or {}).mappings().all()
    return [dict(row) for row in rows]


def get_lesiones_competitions() -> list[dict[str, Any]]:
    sql = """
        SELECT
            nombre,
            codigo
        FROM plantel
        ORDER BY nombre ASC;
    """
    return _fetch_all(sql)


def get_lesiones_players(plantel: str | None = None) -> list[dict[str, Any]]:
    where = ["f.id_estado = 1"]
    params: dict[str, Any] = {}

    if plantel:
        where.append("f.competicion = :plantel")
        params["plantel"] = plantel

    sql = f"""
        SELECT
            f.identificacion AS id_jugadora,
            f.nombre,
            f.apellido,
            f.competicion AS plantel,
            f.fecha_nacimiento,
            i.posicion,
            i.dorsal,
            i.nacionalidad,
            i.foto_url,
            i.foto_url_drive
        FROM futbolistas f
        LEFT JOIN informacion_futbolistas i
            ON f.identificacion = i.identificacion
        WHERE {" AND ".join(where)}
        ORDER BY f.apellido ASC, f.nombre ASC;
    """
    return _fetch_all(sql, params)


def get_lesiones_catalog(table_name: str) -> list[dict[str, Any]]:
    allowed_tables = {
        "segmentos_corporales",
        "zonas_segmento",
        "zonas_anatomicas",
        "mecanismos",
        "tipo_lesion",
        "tipo_especifico_lesion",
        "mecanismo_tipo_lesion",
        "tratamientos",
        "lugares",
    }
    if table_name not in allowed_tables:
        raise ValueError(f"Catalogo no permitido: {table_name}")

    return _fetch_all(f"SELECT * FROM {table_name} ORDER BY id;")


def get_latest_lesion_id_for_player(id_jugadora: str) -> str | None:
    sql = """
        SELECT id_lesion
        FROM lesiones
        WHERE id_jugadora = :id_jugadora
          AND id_lesion IS NOT NULL
        ORDER BY fecha_hora_registro DESC, id DESC
        LIMIT 1;
    """
    rows = _fetch_all(sql, {"id_jugadora": id_jugadora})
    return str(rows[0]["id_lesion"]) if rows else None


def get_lesiones_records(
    plantel: str | None = None,
    user_filter_sql: str = "1=1",
    user_params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    where = [
        "f.id_estado = 1",
        user_filter_sql,
    ]
    params: dict[str, Any] = dict(user_params or {})

    if plantel:
        where.append("f.competicion = :plantel")
        params["plantel"] = plantel

    sql = f"""
        SELECT
            l.id AS id_registro,
            l.id_lesion,
            l.id_jugadora,
            f.nombre,
            f.apellido,
            f.competicion AS plantel,
            f.fecha_nacimiento,
            i.posicion,
            i.dorsal,
            i.nacionalidad,
            i.foto_url,
            i.foto_url_drive,
            l.fecha_lesion,
            l.estado_lesion,
            l.diagnostico,
            l.dias_baja_estimado,
            l.impacto_dias_baja_estimado,
            l.mecanismo_id,
            m.nombre AS mecanismo,
            t.nombre AS tipo_lesion,
            te.nombre AS tipo_especifico,
            l.lugar_id,
            lu.nombre AS lugar,
            l.segmento_id,
            s.nombre AS segmento,
            l.zona_cuerpo_id,
            z.nombre AS zona_cuerpo,
            l.zona_especifica_id,
            za.nombre AS zona_especifica,
            l.lateralidad,
            l.es_recidiva,
            l.tipo_recidiva,
            l.tipo_tratamiento,
            l.personal_reporta,
            l.fecha_alta_diagnostico,
            l.fecha_alta_medica,
            l.fecha_alta_deportiva,
            l.descripcion,
            l.evolucion,
            l.fecha_hora_registro,
            l.usuario
        FROM lesiones l
        LEFT JOIN futbolistas f
            ON l.id_jugadora = f.identificacion
        LEFT JOIN informacion_futbolistas i
            ON l.id_jugadora = i.identificacion
        LEFT JOIN lugares lu
            ON l.lugar_id = lu.id
        LEFT JOIN mecanismos m
            ON l.mecanismo_id = m.id
        LEFT JOIN tipo_lesion t
            ON l.tipo_lesion_id = t.id
        LEFT JOIN tipo_especifico_lesion te
            ON l.tipo_especifico_id = te.id
        LEFT JOIN segmentos_corporales s
            ON l.segmento_id = s.id
        LEFT JOIN zonas_segmento z
            ON l.zona_cuerpo_id = z.id
        LEFT JOIN zonas_anatomicas za
            ON l.zona_especifica_id = za.id
        WHERE {" AND ".join(where)}
        ORDER BY l.fecha_hora_registro DESC;
    """

    return _fetch_all(sql, params)
