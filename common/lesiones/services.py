from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Any

from flask_login import current_user
from sqlalchemy import text

from dux import db
from dux.common.lesiones.queries import get_lesiones_competitions, get_lesiones_records
from dux.common.lesiones.transforms import normalize_records, to_float


DEFAULT_PLANTEL = "1FF"
TEMPORADA_START = date(2025, 7, 16)


def _user_identity() -> tuple[str, str]:
    email = str(getattr(current_user, "email", "") or "").lower()
    role_name = ""
    role_id = getattr(current_user, "role_id", None)

    if role_id is not None:
        row = db.session.execute(
            text("SELECT name FROM roles WHERE id = :role_id LIMIT 1"),
            {"role_id": role_id},
        ).mappings().first()
        if row:
            role_name = str(row["name"] or "").lower()

    return role_name, email


def _build_user_access_filter() -> tuple[str, dict[str, Any]]:
    role_name, email = _user_identity()

    if role_name == "developer":
        return "1=1", {}
    if role_name == "admin":
        return "l.usuario != 'developer@developer.com'", {}
    if email:
        return "LOWER(l.usuario) = :usuario", {"usuario": email}
    return "1=0", {}


def _period_bounds(records: list[dict[str, Any]], period: str) -> tuple[date | None, date | None]:
    dates = [record["fecha_lesion"] for record in records if record.get("fecha_lesion")]
    if not dates:
        return None, None

    ref_date = max(dates)
    if period == "mes":
        return ref_date.replace(day=1), ref_date
    if period == "temporada":
        return TEMPORADA_START, ref_date

    start = ref_date - timedelta(days=ref_date.weekday())
    return start, start + timedelta(days=6)


def _period_filter(records: list[dict[str, Any]], period: str) -> tuple[list[dict[str, Any]], str]:
    start, end = _period_bounds(records, period)
    if not start or not end:
        return [], ""

    filtered = [
        record for record in records
        if record.get("fecha_lesion") and start <= record["fecha_lesion"] <= end
    ]

    if period == "mes":
        label = start.strftime("%m/%Y")
    else:
        label = f"{start.strftime('%d/%m/%Y')} - {end.strftime('%d/%m/%Y')}"

    return filtered, label


def _period_filter_with_bounds(
    records: list[dict[str, Any]],
    period: str,
) -> tuple[list[dict[str, Any]], str, date | None, date | None]:
    start, end = _period_bounds(records, period)
    if not start or not end:
        return [], "", None, None

    filtered = [
        record for record in records
        if record.get("fecha_lesion") and start <= record["fecha_lesion"] <= end
    ]

    if period == "mes":
        label = start.strftime("%m/%Y")
    else:
        label = f"{start.strftime('%d/%m/%Y')} - {end.strftime('%d/%m/%Y')}"

    return filtered, label, start, end


def _mean(values: list[float | None]) -> float:
    clean = [value for value in values if value is not None]
    return round(sum(clean) / len(clean), 1) if clean else 0


def _top_value(records: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values = [
        str(record.get(key) or "").strip()
        for record in records
        if str(record.get(key) or "").strip()
    ]
    if not values:
        return {"label": "-", "count": 0}
    label, count = Counter(values).most_common(1)[0]
    return {"label": label, "count": count}


def _build_kpis(period_records: list[dict[str, Any]], all_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = len(period_records)
    active = sum(1 for record in all_records if record.get("estado_lesion") == "ACTIVO")
    recidivas = sum(1 for record in period_records if record.get("es_recidiva"))
    avg_days = _mean([to_float(record.get("dias_baja_estimado")) for record in period_records])
    graves = sum(
        1 for record in period_records
        if str(record.get("impacto_dias_baja_estimado") or "").strip().upper() in {"GRAVE", "MUY GRAVE"}
    )
    graves_pct = round((graves / total) * 100, 1) if total else 0
    zona_top = _top_value(period_records, "zona_cuerpo")
    tipo_top = _top_value(period_records, "tipo_lesion")
    jugadora_top = _top_value(period_records, "nombre_jugadora")

    return [
        {"label": "Total de lesiones registradas", "value": total, "hint": "En el periodo seleccionado"},
        {"label": "Lesiones activas", "value": active, "hint": "Estado actual global"},
        {"label": "Dias de recuperacion promedio", "value": avg_days, "hint": "Dias de baja estimados"},
        {"label": "Recidivas", "value": recidivas, "hint": "Marcadas como recidiva"},
        {"label": "% lesiones graves/muy graves", "value": f"{graves_pct:.1f}%", "hint": f"{graves} de {total}"},
        {"label": "Zona mas afectada", "value": zona_top["label"], "hint": f"{zona_top['count']} casos"},
        {"label": "Tipo mas frecuente", "value": tipo_top["label"], "hint": f"{tipo_top['count']} casos"},
        {"label": "Jugadora con mas lesiones", "value": jugadora_top["label"], "hint": f"{jugadora_top['count']} casos"},
    ]


def _filter_latest_records(
    records: list[dict[str, Any]],
    jugadora: str | None = None,
    tipo: str | None = None,
    estado: str | None = None,
) -> list[dict[str, Any]]:
    filtered = list(records)
    if jugadora:
        filtered = [record for record in filtered if record.get("nombre_jugadora") == jugadora]
    if tipo:
        filtered = [record for record in filtered if record.get("tipo_lesion") == tipo]
    if estado:
        filtered = [record for record in filtered if record.get("estado_lesion") == estado]
    return filtered


def _unique_options(records: list[dict[str, Any]], key: str) -> list[str]:
    return sorted({
        str(record.get(key) or "").strip()
        for record in records
        if str(record.get(key) or "").strip()
    })


def _filter_groupal_records(
    records: list[dict[str, Any]],
    posicion: str | None = None,
    tipo: str | None = None,
) -> list[dict[str, Any]]:
    filtered = list(records)
    if posicion:
        filtered = [record for record in filtered if record.get("posicion") == posicion]
    if tipo:
        filtered = [record for record in filtered if record.get("tipo_lesion") == tipo]
    return filtered


def _count_with_baja(records: list[dict[str, Any]]) -> int:
    return sum(1 for record in records if (to_float(record.get("dias_baja_estimado")) or 0) > 0)


def _build_groupal_kpis(
    period_records: list[dict[str, Any]],
    base_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    total = len(period_records)
    jugadoras = len({record.get("id_jugadora") for record in period_records if record.get("id_jugadora")})
    active = sum(1 for record in base_records if record.get("estado_lesion") == "ACTIVO")
    recidivas = sum(1 for record in period_records if record.get("es_recidiva"))
    dias_values = [to_float(record.get("dias_baja_estimado")) or 0 for record in period_records]
    dias_total = round(sum(dias_values), 1)
    dias_promedio = _mean(dias_values)
    graves = sum(
        1 for record in period_records
        if str(record.get("impacto_dias_baja_estimado") or "").strip().upper() in {"GRAVE", "MUY GRAVE"}
    )
    con_baja = _count_with_baja(period_records)
    pct_graves = round((graves / total) * 100, 1) if total else 0
    pct_baja = round((con_baja / total) * 100, 1) if total else 0
    tipo_top = _top_value(period_records, "tipo_lesion")
    zona_top = _top_value(period_records, "zona_cuerpo")
    mecanismo_top = _top_value(period_records, "mecanismo")
    lugar_top = _top_value(period_records, "lugar")

    return [
        {"label": "Total de lesiones", "value": total, "hint": "Registros del periodo"},
        {"label": "Jugadoras lesionadas", "value": jugadoras, "hint": "Jugadoras unicas"},
        {"label": "Lesiones activas", "value": active, "hint": "Estado actual filtrado"},
        {"label": "Recidivas", "value": recidivas, "hint": "Marcadas como recidiva"},
        {"label": "Dias de baja totales", "value": dias_total, "hint": "Suma estimada"},
        {"label": "Dias de baja promedio", "value": dias_promedio, "hint": "Promedio estimado"},
        {"label": "% lesiones graves/muy graves", "value": f"{pct_graves:.1f}%", "hint": f"{graves} de {total}"},
        {"label": "% lesiones con baja", "value": f"{pct_baja:.1f}%", "hint": f"{con_baja} de {total}"},
        {"label": "Tipo mas frecuente", "value": tipo_top["label"], "hint": f"{tipo_top['count']} casos"},
        {"label": "Zona mas afectada", "value": zona_top["label"], "hint": f"{zona_top['count']} casos"},
        {"label": "Mecanismo mas frecuente", "value": mecanismo_top["label"], "hint": f"{mecanismo_top['count']} casos"},
        {"label": "Lugar mas frecuente", "value": lugar_top["label"], "hint": f"{lugar_top['count']} casos"},
    ]


def _evolution_bucket(value: date, period: str) -> str:
    if period == "semana":
        return value.strftime("%d/%m")
    if period == "mes":
        week_start = value - timedelta(days=value.weekday())
        return week_start.strftime("%d/%m")
    return value.strftime("%m/%Y")


def _build_evolution_summary_chart(records: list[dict[str, Any]], period: str) -> dict[str, Any]:
    buckets: dict[str, dict[str, Any]] = {}

    for record in records:
        fecha = record.get("fecha_lesion")
        if not fecha:
            continue

        label = _evolution_bucket(fecha, period)
        bucket = buckets.setdefault(
            label,
            {
                "sort": fecha,
                "lesiones": 0,
                "jugadoras": set(),
                "graves": 0,
            },
        )
        bucket["sort"] = min(bucket["sort"], fecha)
        bucket["lesiones"] += 1
        if record.get("id_jugadora"):
            bucket["jugadoras"].add(record["id_jugadora"])
        if str(record.get("impacto_dias_baja_estimado") or "").strip().upper() in {"GRAVE", "MUY GRAVE"}:
            bucket["graves"] += 1

    ordered = sorted(buckets.items(), key=lambda item: item[1]["sort"])
    labels = [label for label, _ in ordered]
    return {
        "labels": labels,
        "lesiones": [data["lesiones"] for _, data in ordered],
        "jugadoras": [len(data["jugadoras"]) for _, data in ordered],
        "graves": [data["graves"] for _, data in ordered],
    }


def build_lesiones_grupal_context(
    plantel: str | None = None,
    posicion: str | None = None,
    tipo: str | None = None,
    period: str = "semana",
) -> dict[str, Any]:
    if period not in {"semana", "mes", "temporada"}:
        period = "semana"

    selected_plantel = plantel or DEFAULT_PLANTEL
    user_filter_sql, user_params = _build_user_access_filter()
    competitions = get_lesiones_competitions()
    plantel_records = normalize_records(
        get_lesiones_records(
            plantel=selected_plantel,
            user_filter_sql=user_filter_sql,
            user_params=user_params,
        )
    )

    position_options = _unique_options(plantel_records, "posicion")
    records_after_position = _filter_groupal_records(plantel_records, posicion=posicion)
    type_options = _unique_options(records_after_position, "tipo_lesion")
    base_records = _filter_groupal_records(plantel_records, posicion=posicion, tipo=tipo)
    period_records, period_label, start_date, end_date = _period_filter_with_bounds(base_records, period)

    return {
        "competitions": competitions,
        "plantel": selected_plantel,
        "period": period,
        "period_label": period_label,
        "start_date": start_date,
        "end_date": end_date,
        "period_options": [
            {"key": "semana", "label": "Semana"},
            {"key": "mes", "label": "Mes"},
            {"key": "temporada", "label": "Temporada"},
        ],
        "filters": {
            "posicion": posicion or "",
            "tipo": tipo or "",
            "posiciones": position_options,
            "tipos": type_options,
        },
        "kpis": _build_groupal_kpis(period_records, base_records),
        "records": period_records[:200],
        "all_records_count": len(plantel_records),
        "filtered_records_count": len(base_records),
        "period_records_count": len(period_records),
        "evolution_chart": _build_evolution_summary_chart(period_records, period),
    }


def build_lesiones_index_context(
    plantel: str | None = None,
    period: str = "semana",
    jugadora: str | None = None,
    tipo: str | None = None,
    estado: str | None = None,
) -> dict[str, Any]:
    if period not in {"semana", "mes", "temporada"}:
        period = "semana"

    selected_plantel = plantel or DEFAULT_PLANTEL
    user_filter_sql, user_params = _build_user_access_filter()
    competitions = get_lesiones_competitions()
    records = normalize_records(
        get_lesiones_records(
            plantel=selected_plantel,
            user_filter_sql=user_filter_sql,
            user_params=user_params,
        )
    )
    period_records, period_label = _period_filter(records, period)
    latest_records = _filter_latest_records(period_records, jugadora=jugadora, tipo=tipo, estado=estado)

    return {
        "competitions": competitions,
        "plantel": selected_plantel,
        "period": period,
        "period_label": period_label,
        "period_options": [
            {"key": "semana", "label": "Semana"},
            {"key": "mes", "label": "Mes"},
            {"key": "temporada", "label": "Temporada"},
        ],
        "kpis": _build_kpis(period_records, records),
        "latest_records": latest_records[:50],
        "all_records_count": len(records),
        "period_records_count": len(period_records),
        "filters": {
            "jugadora": jugadora or "",
            "tipo": tipo or "",
            "estado": estado or "",
            "jugadoras": _unique_options(period_records, "nombre_jugadora"),
            "tipos": _unique_options(period_records, "tipo_lesion"),
            "estados": _unique_options(period_records, "estado_lesion"),
        },
    }
