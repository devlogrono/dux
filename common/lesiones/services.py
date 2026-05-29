from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from flask_login import current_user
from sqlalchemy import text

from dux import db
from dux.common.lesiones.queries import get_lesiones_competitions, get_lesiones_records
from dux.common.lesiones.transforms import coerce_date, normalize_records, to_float


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


def _month_add(value: date, months: int) -> date:
    year = value.year + ((value.month - 1 + months) // 12)
    month = ((value.month - 1 + months) % 12) + 1
    day = min(value.day, _month_last_day(year, month))
    return date(year, month, day)


def _month_last_day(year: int, month: int) -> int:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    return (next_month - timedelta(days=1)).day


def _period_overview(
    records: list[dict[str, Any]],
    period: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, date | None, date | None]:
    start, end = _period_bounds(records, period)
    if not start or not end:
        return [], [], "", None, None

    current = [
        record for record in records
        if record.get("fecha_lesion") and start <= record["fecha_lesion"] <= end
    ]

    previous: list[dict[str, Any]] = []
    if period == "semana":
        prev_start = start - timedelta(days=7)
        prev_end = start - timedelta(days=1)
        previous = [
            record for record in records
            if record.get("fecha_lesion") and prev_start <= record["fecha_lesion"] <= prev_end
        ]
    elif period == "mes":
        prev_end = start - timedelta(days=1)
        prev_start = prev_end.replace(day=1)
        previous = [
            record for record in records
            if record.get("fecha_lesion") and prev_start <= record["fecha_lesion"] <= prev_end
        ]

    if period == "mes":
        label = start.strftime("%m/%Y")
    else:
        label = f"{start.strftime('%d/%m/%Y')} - {end.strftime('%d/%m/%Y')}"

    return current, previous, label, start, end


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


def _format_delta(value: float | int | None, singular: str, plural: str | None = None) -> str | None:
    if value is None:
        return None
    plural = plural or singular
    value_fmt: float | int
    if isinstance(value, float) and not value.is_integer():
        value_fmt = round(value, 1)
    else:
        value_fmt = int(value)
    sign = "+" if value_fmt > 0 else ""
    unit = singular if abs(value_fmt) == 1 else plural
    return f"{sign}{value_fmt} {unit}"


def _chart_bucket(value: date, freq: str) -> str:
    if freq == "W":
        week_start = value - timedelta(days=value.weekday())
        return week_start.strftime("%d/%m")
    return value.strftime("%m/%Y")


def _build_series_counts(records: list[dict[str, Any]], freq: str) -> list[int]:
    buckets: dict[str, dict[str, Any]] = {}
    for record in records:
        fecha = record.get("fecha_lesion")
        if not fecha:
            continue
        label = _chart_bucket(fecha, freq)
        bucket = buckets.setdefault(label, {"sort": fecha, "value": 0})
        bucket["sort"] = min(bucket["sort"], fecha)
        bucket["value"] += 1
    return [bucket["value"] for _, bucket in sorted(buckets.items(), key=lambda item: item[1]["sort"])]


def _build_series_avg_days(records: list[dict[str, Any]], freq: str) -> list[float]:
    buckets: dict[str, dict[str, Any]] = {}
    for record in records:
        fecha = record.get("fecha_lesion")
        if not fecha:
            continue
        label = _chart_bucket(fecha, freq)
        bucket = buckets.setdefault(label, {"sort": fecha, "values": []})
        bucket["sort"] = min(bucket["sort"], fecha)
        value = to_float(record.get("dias_baja_estimado"))
        if value is not None:
            bucket["values"].append(value)
    return [
        round(sum(bucket["values"]) / len(bucket["values"]), 1) if bucket["values"] else 0
        for _, bucket in sorted(buckets.items(), key=lambda item: item[1]["sort"])
    ]


def _overview_chart_records(records: list[dict[str, Any]], period: str, start: date | None) -> tuple[list[dict[str, Any]], str]:
    if not start:
        return [], "M"
    if period == "semana":
        return [record for record in records if record.get("fecha_lesion") and record["fecha_lesion"] >= start - timedelta(days=56)], "W"
    if period == "mes":
        return [record for record in records if record.get("fecha_lesion") and record["fecha_lesion"] >= _month_add(start, -8)], "M"
    return records, "M"


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


def _build_kpis(
    period_records: list[dict[str, Any]],
    all_records: list[dict[str, Any]],
    previous_records: list[dict[str, Any]] | None = None,
    period: str = "semana",
    period_start: date | None = None,
) -> list[dict[str, Any]]:
    previous_records = previous_records or []
    total = len(period_records)
    active = sum(1 for record in all_records if record.get("estado_lesion") == "ACTIVO")
    recidivas = sum(1 for record in period_records if record.get("es_recidiva"))
    avg_days = _mean([to_float(record.get("dias_baja_estimado")) for record in period_records])
    prev_avg_days = _mean([to_float(record.get("dias_baja_estimado")) for record in previous_records])
    chart_records, chart_freq = _overview_chart_records(all_records, period, period_start)
    total_delta = total - len(previous_records) if period in {"semana", "mes"} else None
    recidivas_delta = recidivas - sum(1 for record in previous_records if record.get("es_recidiva")) if period in {"semana", "mes"} else None
    days_delta = round(avg_days - prev_avg_days, 1) if period in {"semana", "mes"} else None
    graves = sum(
        1 for record in period_records
        if str(record.get("impacto_dias_baja_estimado") or "").strip().upper() in {"GRAVE", "MUY GRAVE"}
    )
    graves_pct = round((graves / total) * 100, 1) if total else 0
    zona_top = _top_value(period_records, "zona_cuerpo")
    tipo_top = _top_value(period_records, "tipo_lesion")
    jugadora_top = _top_value(period_records, "nombre_jugadora")

    return [
        {
            "label": "Total de lesiones registradas",
            "value": total,
            "hint": "En el periodo seleccionado",
            "delta": _format_delta(total_delta, "caso", "casos"),
            "delta_inverse": True,
            "chart": _build_series_counts(chart_records, chart_freq),
        },
        {"label": "Lesiones activas", "value": active, "hint": "Estado actual global"},
        {
            "label": "Dias de recuperacion promedio",
            "value": avg_days,
            "hint": "Dias de baja estimados",
            "delta": _format_delta(days_delta, "dia", "dias"),
            "delta_inverse": True,
            "chart": _build_series_avg_days(chart_records, chart_freq),
        },
        {
            "label": "Recidivas",
            "value": recidivas,
            "hint": "Marcadas como recidiva",
            "delta": _format_delta(recidivas_delta, "caso", "casos"),
            "delta_inverse": True,
        },
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


def _calculate_age(value: Any) -> int | None:
    birth_date = coerce_date(value)
    if birth_date is None:
        return None
    today = datetime.today().date()
    return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))


def _build_player_options(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    players: dict[str, dict[str, Any]] = {}
    for record in records:
        player_id = str(record.get("id_jugadora") or "").strip()
        if not player_id or player_id in players:
            continue
        players[player_id] = {
            "id": player_id,
            "nombre": record.get("nombre_jugadora") or player_id,
        }
    return sorted(players.values(), key=lambda item: item["nombre"])


def _build_player_options_by_name(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    players: dict[str, dict[str, Any]] = {}
    for record in records:
        name = str(record.get("nombre_jugadora") or "").strip()
        player_id = str(record.get("id_jugadora") or "").strip()
        if not name or not player_id or name in players:
            continue
        players[name] = {"id": player_id, "nombre": name}
    return sorted(players.values(), key=lambda item: item["nombre"])


def _select_player_id(records: list[dict[str, Any]], selected: str | None = None) -> str | None:
    valid_ids = {str(record.get("id_jugadora")) for record in records if record.get("id_jugadora")}
    if selected and selected in valid_ids:
        return selected
    dated = sorted(
        [record for record in records if record.get("id_jugadora")],
        key=lambda record: (record.get("fecha_lesion") is not None, record.get("fecha_lesion")),
        reverse=True,
    )
    return str(dated[0]["id_jugadora"]) if dated else None


def _build_player_card(player_records: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not player_records:
        return None
    base = player_records[0]
    fecha_nacimiento = coerce_date(base.get("fecha_nacimiento"))
    foto_proxy_url = None
    if base.get("foto_url") or base.get("foto_url_drive"):
        foto_proxy_url = "dashboard_physical.player_photo"

    return {
        "id_jugadora": base.get("id_jugadora"),
        "nombre": base.get("nombre_jugadora") or "-",
        "dorsal": base.get("dorsal"),
        "identificacion": base.get("id_jugadora") or "-",
        "pais": base.get("nacionalidad") or "-",
        "plantel": base.get("plantel") or "-",
        "posicion": base.get("posicion") or "-",
        "fecha_nacimiento": fecha_nacimiento,
        "edad": _calculate_age(fecha_nacimiento),
        "has_photo": bool(foto_proxy_url),
    }


def _build_individual_kpis(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = len(records)
    active = sum(1 for record in records if record.get("estado_lesion") == "ACTIVO")
    recidivas = sum(1 for record in records if record.get("es_recidiva"))
    dias_values = [to_float(record.get("dias_baja_estimado")) or 0 for record in records]
    dias_total = round(sum(dias_values), 1)
    dias_promedio = _mean(dias_values)
    graves = sum(
        1 for record in records
        if str(record.get("impacto_dias_baja_estimado") or "").strip().upper() in {"GRAVE", "MUY GRAVE"}
    )
    pct_graves = round((graves / total) * 100, 1) if total else 0
    zona_top = _top_value(records, "zona_cuerpo")
    tipo_top = _top_value(records, "tipo_lesion")

    return [
        {"label": "Total de lesiones registradas", "value": total, "hint": "Historial filtrado"},
        {"label": "Lesiones activas", "value": active, "hint": "Estado actual"},
        {"label": "Dias de baja totales", "value": dias_total, "hint": "Suma estimada"},
        {"label": "Recidivas", "value": recidivas, "hint": "Marcadas como recidiva"},
        {"label": "Dias de baja promedio", "value": dias_promedio, "hint": "Promedio estimado"},
        {"label": "% lesiones graves/muy graves", "value": f"{pct_graves:.1f}%", "hint": f"{graves} de {total}"},
        {"label": "Zona mas afectada", "value": zona_top["label"], "hint": f"{zona_top['count']} casos"},
        {"label": "Tipo mas frecuente", "value": tipo_top["label"], "hint": f"{tipo_top['count']} casos"},
    ]


def _build_individual_history_chart(records: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(
        [record for record in records if record.get("fecha_lesion")],
        key=lambda record: record["fecha_lesion"],
    )
    return {
        "labels": [record["fecha_lesion"].strftime("%Y-%m-%d") for record in ordered],
        "dias": [to_float(record.get("dias_baja_estimado")) or 0 for record in ordered],
        "tipos": [record.get("tipo_lesion") or "Lesion" for record in ordered],
        "gravedad": [record.get("impacto_dias_baja_estimado") or "-" for record in ordered],
    }


def build_lesiones_individual_context(
    plantel: str | None = None,
    posicion: str | None = None,
    jugadora: str | None = None,
    tipo: str | None = None,
) -> dict[str, Any]:
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
    player_options = _build_player_options(records_after_position)
    selected_player_id = _select_player_id(records_after_position, jugadora)
    player_records_base = [
        record for record in records_after_position
        if selected_player_id and str(record.get("id_jugadora")) == selected_player_id
    ]
    type_options = _unique_options(player_records_base, "tipo_lesion")
    player_records = _filter_groupal_records(player_records_base, tipo=tipo)

    return {
        "competitions": competitions,
        "plantel": selected_plantel,
        "filters": {
            "posicion": posicion or "",
            "jugadora": selected_player_id or "",
            "tipo": tipo or "",
            "posiciones": position_options,
            "jugadoras": player_options,
            "tipos": type_options,
        },
        "player": _build_player_card(player_records_base),
        "kpis": _build_individual_kpis(player_records),
        "records": player_records[:200],
        "all_records_count": len(plantel_records),
        "player_records_count": len(player_records_base),
        "filtered_records_count": len(player_records),
        "history_chart": _build_individual_history_chart(player_records),
    }


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
    period_records, previous_records, period_label, period_start, _ = _period_overview(records, period)
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
        "kpis": _build_kpis(
            period_records,
            records,
            previous_records=previous_records,
            period=period,
            period_start=period_start,
        ),
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
            "individual_options": _build_player_options_by_name(latest_records),
        },
    }
