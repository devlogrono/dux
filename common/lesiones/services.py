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
        filtered = [
            record for record in filtered
            if str(record.get("posicion") or "").strip() == str(posicion).strip()
        ]
    if tipo:
        filtered = [
            record for record in filtered
            if str(record.get("tipo_lesion") or "").strip() == str(tipo).strip()
        ]
    return filtered


def _count_with_baja(records: list[dict[str, Any]]) -> int:
    return sum(1 for record in records if (to_float(record.get("dias_baja_estimado")) or 0) > 0)


def _build_groupal_kpis(
    period_records: list[dict[str, Any]],
    base_records: list[dict[str, Any]],
    previous_records: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    show_deltas = previous_records is not None
    previous_records = previous_records or []
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

    prev_total = len(previous_records)
    prev_jugadoras = len({record.get("id_jugadora") for record in previous_records if record.get("id_jugadora")})
    prev_recidivas = sum(1 for record in previous_records if record.get("es_recidiva"))
    prev_dias_values = [to_float(record.get("dias_baja_estimado")) or 0 for record in previous_records]
    prev_dias_total = round(sum(prev_dias_values), 1)
    prev_dias_promedio = _mean(prev_dias_values)
    prev_graves = sum(
        1 for record in previous_records
        if str(record.get("impacto_dias_baja_estimado") or "").strip().upper() in {"GRAVE", "MUY GRAVE"}
    )
    prev_con_baja = _count_with_baja(previous_records)
    prev_pct_graves = round((prev_graves / prev_total) * 100, 1) if prev_total else 0
    prev_pct_baja = round((prev_con_baja / prev_total) * 100, 1) if prev_total else 0

    return [
        {"label": "Total de lesiones", "value": total, "hint": "Registros del periodo", "delta": _format_delta(total - prev_total, "caso", "casos") if show_deltas else None},
        {"label": "Jugadoras lesionadas", "value": jugadoras, "hint": "Jugadoras unicas", "delta": _format_delta(jugadoras - prev_jugadoras, "caso", "casos") if show_deltas else None},
        {"label": "Lesiones activas", "value": active, "hint": "Estado actual filtrado"},
        {"label": "Recidivas", "value": recidivas, "hint": f"{round((recidivas / total) * 100, 1) if total else 0:.1f}%", "delta": _format_delta(recidivas - prev_recidivas, "caso", "casos") if show_deltas else None},
        {"label": "Dias de baja totales", "value": dias_total, "hint": "Suma estimada", "delta": _format_delta(dias_total - prev_dias_total, "dia", "dias") if show_deltas else None},
        {"label": "Dias de baja promedio", "value": dias_promedio, "hint": "Promedio estimado", "delta": _format_delta(dias_promedio - prev_dias_promedio, "dia", "dias") if show_deltas else None},
        {"label": "% lesiones graves/muy graves", "value": f"{pct_graves:.1f}%", "hint": f"{graves} de {total}", "delta": f"{pct_graves - prev_pct_graves:+.1f} pp" if show_deltas else None},
        {"label": "% lesiones con baja", "value": f"{pct_baja:.1f}%", "hint": f"{con_baja} de {total}", "delta": f"{pct_baja - prev_pct_baja:+.1f} pp" if show_deltas else None},
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


def _parse_date_param(value: str | None) -> date | None:
    if not value:
        return None
    return coerce_date(value)


def _date_range(start: date, end: date) -> list[date]:
    days = (end - start).days
    if days < 0:
        return []
    return [start + timedelta(days=i) for i in range(days + 1)]


def _week_labels_for_month(start: date, end: date) -> list[tuple[str, int]]:
    max_week = ((end.day - 1) // 7) + 1
    labels = []
    for week in range(1, max_week + 1):
        first = (week - 1) * 7 + 1
        last = min(week * 7, end.day)
        labels.append((f"S{week} ({first}-{last})", week))
    return labels


def _month_range(start: date, end: date) -> list[date]:
    current = start.replace(day=1)
    stop = end.replace(day=1)
    values = []
    while current <= stop:
        values.append(current)
        current = _month_add(current, 1)
    return values


def _time_bucket_config(start: date | None, end: date | None) -> str:
    if not start or not end:
        return "M"
    duration = (end - start).days + 1
    if duration <= 7:
        return "D"
    if duration <= 31:
        return "WEEK_IN_MONTH"
    return "M"


def _empty_bucket_map(start: date | None, end: date | None, mode: str) -> dict[str, dict[str, Any]]:
    if not start or not end:
        return {}
    if mode == "D":
        return {
            value.strftime("%d/%m"): {"sort": value}
            for value in _date_range(start, end)
        }
    if mode == "WEEK_IN_MONTH":
        return {
            label: {"sort": week}
            for label, week in _week_labels_for_month(start, end)
        }
    return {
        value.strftime("%m/%Y"): {"sort": value}
        for value in _month_range(start, end)
    }


def _record_bucket_label(value: date, mode: str, end: date | None = None) -> str:
    if mode == "D":
        return value.strftime("%d/%m")
    if mode == "WEEK_IN_MONTH":
        week = ((value.day - 1) // 7) + 1
        first = (week - 1) * 7 + 1
        period_last_day = end.day if end and end.year == value.year and end.month == value.month else _month_last_day(value.year, value.month)
        last = min(week * 7, period_last_day)
        return f"S{week} ({first}-{last})"
    return value.strftime("%m/%Y")


def _build_evolution_charts(records: list[dict[str, Any]], start: date | None, end: date | None) -> dict[str, Any]:
    empty = {
        "summary": {"labels": [], "lesiones": [], "jugadoras": [], "graves": [], "muy_graves": []},
        "dias_baja": {"labels": [], "values": []},
        "nuevas_recidivas": {"labels": [], "nuevas": [], "recidivas": []},
    }
    if not records:
        return empty

    mode = _time_bucket_config(start, end)
    base = _empty_bucket_map(start, end, mode)
    if not base:
        return empty

    summary = {
        label: {
            **meta,
            "lesiones": 0,
            "jugadoras": set(),
            "graves": 0,
            "muy_graves": 0,
            "dias_baja": 0.0,
            "nuevas": 0,
            "recidivas": 0,
        }
        for label, meta in base.items()
    }

    for record in records:
        fecha = record.get("fecha_lesion")
        if not fecha:
            continue
        label = _record_bucket_label(fecha, mode, end=end)
        if label not in summary:
            continue
        bucket = summary[label]
        bucket["lesiones"] += 1
        if record.get("id_jugadora"):
            bucket["jugadoras"].add(record["id_jugadora"])
        gravedad = str(record.get("impacto_dias_baja_estimado") or "").strip().upper()
        if gravedad == "GRAVE":
            bucket["graves"] += 1
        elif gravedad == "MUY GRAVE":
            bucket["muy_graves"] += 1
        bucket["dias_baja"] += to_float(record.get("dias_baja_estimado")) or 0
        if record.get("es_recidiva"):
            bucket["recidivas"] += 1
        else:
            bucket["nuevas"] += 1

    ordered = sorted(summary.items(), key=lambda item: item[1]["sort"])
    labels = [label for label, _ in ordered]
    return {
        "summary": {
            "labels": labels,
            "lesiones": [data["lesiones"] for _, data in ordered],
            "jugadoras": [len(data["jugadoras"]) for _, data in ordered],
            "graves": [data["graves"] for _, data in ordered],
            "muy_graves": [data["muy_graves"] for _, data in ordered],
        },
        "dias_baja": {
            "labels": labels,
            "values": [round(data["dias_baja"], 1) for _, data in ordered],
        },
        "nuevas_recidivas": {
            "labels": labels,
            "nuevas": [data["nuevas"] for _, data in ordered],
            "recidivas": [data["recidivas"] for _, data in ordered],
        },
    }


def _severity_label(record: dict[str, Any]) -> str:
    severity = str(record.get("impacto_dias_baja_estimado") or "").strip().upper()
    dias_baja = to_float(record.get("dias_baja_estimado")) or 0
    if not severity and dias_baja == 0:
        return "SIN BAJA"
    if severity in {"MENOR", "MINIMA", "MÍNIMA"}:
        return "LEVE"
    return severity or "N/A"


def _category_counts(records: list[dict[str, Any]], *keys: str) -> dict[tuple[str, ...], int]:
    counts: dict[tuple[str, ...], int] = {}
    for record in records:
        values = tuple(str(record.get(key) or "N/A").strip() or "N/A" for key in keys)
        counts[values] = counts.get(values, 0) + 1
    return counts


def _ordered_categories_from_counts(counts: dict[tuple[str, ...], int], index: int = 0) -> list[str]:
    totals: dict[str, int] = {}
    for key, value in counts.items():
        totals[key[index]] = totals.get(key[index], 0) + value
    return [key for key, _ in sorted(totals.items(), key=lambda item: (item[1], item[0]))]


def _stacked_count_chart(
    counts: dict[tuple[str, str], int],
    labels: list[str],
    series_order: list[str],
) -> dict[str, Any]:
    return {
        "labels": labels,
        "datasets": [
            {
                "label": serie,
                "data": [counts.get((label, serie), 0) for label in labels],
            }
            for serie in series_order
        ],
    }


def _build_distribution_charts(records: list[dict[str, Any]]) -> dict[str, Any]:
    severity_order = ["SIN BAJA", "LEVE", "MODERADA", "GRAVE", "MUY GRAVE", "N/A"]
    severity_counts: dict[tuple[str, str], int] = {}
    for record in records:
        tipo = str(record.get("tipo_lesion") or "N/A").strip() or "N/A"
        severity = _severity_label(record)
        severity_counts[(tipo, severity)] = severity_counts.get((tipo, severity), 0) + 1
    severity_labels = _ordered_categories_from_counts(severity_counts)

    lugar_counts = _category_counts(records, "lugar", "mecanismo")
    lugar_labels = sorted({key[0] for key in lugar_counts})
    mecanismos = sorted({key[1] for key in lugar_counts})

    recidiva_counts: dict[tuple[str, str], int] = {}
    for record in records:
        tipo = str(record.get("tipo_lesion") or "N/A").strip() or "N/A"
        caso = "Recidiva" if record.get("es_recidiva") else "Nueva"
        recidiva_counts[(tipo, caso)] = recidiva_counts.get((tipo, caso), 0) + 1
    recidiva_labels = _ordered_categories_from_counts(recidiva_counts)

    return {
        "tipo_severidad": _stacked_count_chart(severity_counts, severity_labels, severity_order),
        "lugar_mecanismo": _stacked_count_chart(lugar_counts, lugar_labels, mecanismos),
        "tipo_recidiva": _stacked_count_chart(recidiva_counts, recidiva_labels, ["Nueva", "Recidiva"]),
    }


ZONA_COLOR_MAP = {
    "CARA": "#76b7eb",
    "CRÁNEO": "#9ecae1",
    "CRANEO": "#9ecae1",
    "COLUMNA CERVICAL": "#8c564b",
    "COLUMNA DORSAL": "#2ca89c",
    "TÓRAX": "#f39c12",
    "TORAX": "#f39c12",
    "COLUMNA LUMBAR": "#7f7f7f",
    "PÉLVIS": "#9467bd",
    "PELVIS": "#9467bd",
    "CINTURA ESCAPULAR Y HOMBRO": "#ff2b2b",
    "BRAZO": "#1f77b4",
    "CODO": "#f2a7a7",
    "ANTEBRAZO": "#17becf",
    "MUÑECA": "#bcbd22",
    "MUNECA": "#bcbd22",
    "MANO": "#e377c2",
    "CADERA": "#4e79a7",
    "MUSLO": "#6bdc8b",
    "RODILLA": "#59a14f",
    "PIERNA": "#8cd17d",
    "TOBILLO": "#6f42c1",
    "PIE": "#f4c95d",
    "N/A": "#95a5a6",
}


def _color_for_category(value: str, index: int) -> str:
    fallback = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ab"]
    return ZONA_COLOR_MAP.get(value.upper(), fallback[index % len(fallback)])


def _build_days_by_type_zone(records: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[tuple[str, str], float] = {}
    for record in records:
        tipo = str(record.get("tipo_lesion") or "N/A").strip() or "N/A"
        zona = str(record.get("zona_cuerpo") or "N/A").strip() or "N/A"
        totals[(tipo, zona)] = totals.get((tipo, zona), 0) + (to_float(record.get("dias_baja_estimado")) or 0)

    label_totals: dict[str, float] = {}
    for (tipo, _zona), value in totals.items():
        label_totals[tipo] = label_totals.get(tipo, 0) + value
    labels = [key for key, _ in sorted(label_totals.items(), key=lambda item: (item[1], item[0]))]
    zonas = sorted({zona for _, zona in totals})
    return {
        "labels": labels,
        "datasets": [
            {
                "label": zona,
                "data": [round(totals.get((label, zona), 0), 1) for label in labels],
                "backgroundColor": _color_for_category(zona, index),
            }
            for index, zona in enumerate(zonas)
        ],
    }


def _build_specific_zone_detail(
    records: list[dict[str, Any]],
    tipo_selected: str | None = None,
    zona_selected: str | None = None,
) -> dict[str, Any]:
    filtered = list(records)
    if tipo_selected:
        filtered = [record for record in filtered if str(record.get("tipo_lesion") or "").strip() == tipo_selected]
    if zona_selected:
        filtered = [record for record in filtered if str(record.get("zona_cuerpo") or "").strip() == zona_selected]

    totals: dict[tuple[str, str], float] = {}
    for record in filtered:
        zona_especifica = str(record.get("zona_especifica") or "N/A").strip() or "N/A"
        zona = str(record.get("zona_cuerpo") or "N/A").strip() or "N/A"
        totals[(zona_especifica, zona)] = totals.get((zona_especifica, zona), 0) + (to_float(record.get("dias_baja_estimado")) or 0)

    label_totals: dict[str, float] = {}
    label_zone: dict[str, str] = {}
    for (zona_especifica, zona), value in totals.items():
        label_totals[zona_especifica] = label_totals.get(zona_especifica, 0) + value
        label_zone.setdefault(zona_especifica, zona)
    labels = [key for key, _ in sorted(label_totals.items(), key=lambda item: (item[1], item[0]))]
    return {
        "labels": labels,
        "values": [round(label_totals[label], 1) for label in labels],
        "colors": [_color_for_category(label_zone.get(label, "N/A"), index) for index, label in enumerate(labels)],
    }


def _build_impact_charts(
    records: list[dict[str, Any]],
    tipo_selected: str | None = None,
    zona_selected: str | None = None,
) -> dict[str, Any]:
    tipo_options = _unique_options(records, "tipo_lesion")
    zona_options = _unique_options(records, "zona_cuerpo")
    if tipo_selected and tipo_selected not in tipo_options:
        tipo_selected = None
    if zona_selected and zona_selected not in zona_options:
        zona_selected = None

    return {
        "tipo_zona": _build_days_by_type_zone(records),
        "detalle": _build_specific_zone_detail(records, tipo_selected=tipo_selected, zona_selected=zona_selected),
        "filters": {
            "tipo": tipo_selected or "",
            "zona": zona_selected or "",
            "tipos": tipo_options,
            "zonas": zona_options,
        },
    }


def _build_players_scatter(records: list[dict[str, Any]]) -> dict[str, Any]:
    players: dict[str, dict[str, Any]] = {}
    for record in records:
        player = str(record.get("nombre_jugadora") or "N/A").strip() or "N/A"
        item = players.setdefault(player, {"jugadora": player, "total_lesiones": 0, "dias_baja": 0.0})
        item["total_lesiones"] += 1
        item["dias_baja"] += to_float(record.get("dias_baja_estimado")) or 0

    rows = sorted(players.values(), key=lambda item: (item["total_lesiones"], item["dias_baja"], item["jugadora"]))
    return {
        "points": [
            {
                "x": item["total_lesiones"],
                "y": round(item["dias_baja"], 1),
                "jugadora": item["jugadora"],
            }
            for item in rows
        ]
    }


def _build_specific_type_heatmap(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[tuple[str, str], int] = {}
    for record in records:
        tipo = str(record.get("tipo_lesion") or "N/A").strip() or "N/A"
        tipo_especifico = str(record.get("tipo_especifico") or "N/A").strip() or "N/A"
        counts[(tipo, tipo_especifico)] = counts.get((tipo, tipo_especifico), 0) + 1

    type_totals: dict[str, int] = {}
    specific_totals: dict[str, int] = {}
    for (tipo, tipo_especifico), total in counts.items():
        type_totals[tipo] = type_totals.get(tipo, 0) + total
        specific_totals[tipo_especifico] = specific_totals.get(tipo_especifico, 0) + total

    types = [key for key, _ in sorted(type_totals.items(), key=lambda item: (-item[1], item[0]))]
    specifics = [key for key, _ in sorted(specific_totals.items(), key=lambda item: (-item[1], item[0]))]
    max_value = max(counts.values(), default=0)
    rows = []
    for tipo in types:
        cells = []
        for tipo_especifico in specifics:
            value = counts.get((tipo, tipo_especifico), 0)
            intensity = value / max_value if max_value else 0
            cells.append({
                "tipo_especifico": tipo_especifico,
                "value": value,
                "intensity": round(intensity, 3),
            })
        rows.append({"tipo_lesion": tipo, "cells": cells})

    return {
        "types": types,
        "specifics": specifics,
        "rows": rows,
        "max_value": max_value,
    }


def _build_recurrence_type_heatmap(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[tuple[str, str], int] = {}
    for record in records:
        if not record.get("es_recidiva"):
            continue
        tipo_recidiva = str(record.get("tipo_recidiva") or "").strip().upper()
        if not tipo_recidiva:
            continue
        tipo = str(record.get("tipo_lesion") or "N/A").strip() or "N/A"
        counts[(tipo, tipo_recidiva)] = counts.get((tipo, tipo_recidiva), 0) + 1

    type_totals: dict[str, int] = {}
    for (tipo, _tipo_recidiva), total in counts.items():
        type_totals[tipo] = type_totals.get(tipo, 0) + total

    types = [key for key, _ in sorted(type_totals.items(), key=lambda item: (-item[1], item[0]))]
    recurrence_order = ["TEMPRANA (≤ 2 MESES)", "TARDÍA (2-12 MESES)"]
    present_recurrences = sorted({tipo_recidiva for _, tipo_recidiva in counts})
    recurrences = [item for item in recurrence_order if item in present_recurrences]
    recurrences.extend(item for item in present_recurrences if item not in recurrences)

    max_value = max(counts.values(), default=0)
    rows = []
    for tipo in types:
        cells = []
        for tipo_recidiva in recurrences:
            value = counts.get((tipo, tipo_recidiva), 0)
            intensity = value / max_value if max_value else 0
            cells.append({
                "tipo_recidiva": tipo_recidiva,
                "value": value,
                "intensity": round(intensity, 3),
            })
        rows.append({"tipo_lesion": tipo, "cells": cells})

    return {
        "types": types,
        "recurrences": recurrences,
        "rows": rows,
        "max_value": max_value,
    }


def _sort_records_by_injury_date(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        records,
        key=lambda record: (record.get("fecha_lesion") is not None, record.get("fecha_lesion")),
        reverse=True,
    )


def _build_groupal_table_context(
    records: list[dict[str, Any]],
    estado_selected: str | None = None,
    severidad_selected: str | None = None,
) -> dict[str, Any]:
    estado_options = _unique_options(records, "estado_lesion")
    raw_severities = {
        _severity_label(record)
        for record in records
        if _severity_label(record)
    }
    severity_order = ["SIN BAJA", "LEVE", "MODERADA", "GRAVE", "MUY GRAVE", "N/A"]
    severity_values = [value for value in severity_order if value in raw_severities]
    severity_values.extend(sorted(value for value in raw_severities if value not in severity_values))

    if estado_selected and estado_selected not in estado_options:
        estado_selected = None
    if severidad_selected and severidad_selected not in severity_values:
        severidad_selected = None

    filtered = list(records)
    if estado_selected:
        filtered = [
            record for record in filtered
            if str(record.get("estado_lesion") or "").strip() == estado_selected
        ]
    if severidad_selected:
        filtered = [
            record for record in filtered
            if _severity_label(record) == severidad_selected
        ]

    return {
        "records": _sort_records_by_injury_date(filtered)[:200],
        "filters": {
            "estado": estado_selected or "",
            "severidad": severidad_selected or "",
            "estados": estado_options,
            "severidades": severity_values,
        },
    }


def _resolve_groupal_period(
    records: list[dict[str, Any]],
    period: str,
    custom_start: date | None = None,
    custom_end: date | None = None,
) -> tuple[list[dict[str, Any]], str, date | None, date | None]:
    dated = [record for record in records if record.get("fecha_lesion")]
    if not dated:
        return [], "", None, None

    min_date = min(record["fecha_lesion"] for record in dated)
    max_date = max(record["fecha_lesion"] for record in dated)
    today = datetime.today().date()
    ref = max_date

    if period == "semana":
        start = ref - timedelta(days=ref.weekday())
        end = min(start + timedelta(days=6), ref)
    elif period == "mes":
        start = ref.replace(day=1)
        end = ref
    elif period == "ultimos_3_meses":
        end = today
        start = date(_month_add(end, -2).year, _month_add(end, -2).month, 1)
    elif period == "ultimos_6_meses":
        end = today
        start = date(_month_add(end, -5).year, _month_add(end, -5).month, 1)
    elif period == "temporada":
        start = TEMPORADA_START
        end = today
    elif period == "todo":
        start = min_date
        end = max_date
    elif period == "personalizado":
        start = custom_start or min_date
        end = custom_end or max_date
    else:
        start = ref.replace(day=1)
        end = ref

    start = max(start, min_date)
    end = min(end, today)
    if start > end:
        start = min_date
        end = min(ref, today)

    filtered = [
        record for record in dated
        if start <= record["fecha_lesion"] <= end
    ]
    label = f"{start.strftime('%d/%m/%Y')} - {end.strftime('%d/%m/%Y')}"
    return filtered, label, start, end


def _previous_groupal_period(
    base_records: list[dict[str, Any]],
    period: str,
    start: date | None,
    end: date | None,
) -> list[dict[str, Any]] | None:
    if not start or not end or period not in {"semana", "mes", "ultimos_3_meses", "ultimos_6_meses"}:
        return None

    if period == "semana":
        prev_start = start - timedelta(days=7)
        prev_end = end - timedelta(days=7)
    elif period == "mes":
        prev_end = start - timedelta(days=1)
        prev_start = prev_end.replace(day=1)
    else:
        months = 3 if period == "ultimos_3_meses" else 6
        prev_end = start - timedelta(days=1)
        prev_start_anchor = _month_add(prev_end, -(months - 1))
        prev_start = date(prev_start_anchor.year, prev_start_anchor.month, 1)

    return [
        record for record in base_records
        if record.get("fecha_lesion") and prev_start <= record["fecha_lesion"] <= prev_end
    ]


def build_lesiones_grupal_context(
    plantel: str | None = None,
    posicion: str | None = None,
    tipo: str | None = None,
    period: str = "mes",
    start_date: str | None = None,
    end_date: str | None = None,
    impact_tipo: str | None = None,
    impact_zona: str | None = None,
    registro_estado: str | None = None,
    registro_severidad: str | None = None,
    active_tab: str = "evolucion",
) -> dict[str, Any]:
    if period not in {"semana", "mes", "ultimos_3_meses", "ultimos_6_meses", "temporada", "todo", "personalizado"}:
        period = "mes"
    if active_tab not in {"evolucion", "distribucion", "impacto", "jugadoras", "tipo-especifico", "recidivas", "registros"}:
        active_tab = "evolucion"

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
    if posicion and posicion not in position_options:
        posicion = None

    records_after_position = _filter_groupal_records(plantel_records, posicion=posicion)
    type_options = _unique_options(records_after_position, "tipo_lesion")
    if tipo and tipo not in type_options:
        tipo = None

    base_records = _filter_groupal_records(plantel_records, posicion=posicion, tipo=tipo)
    custom_start = _parse_date_param(start_date)
    custom_end = _parse_date_param(end_date)
    period_records, period_label, resolved_start, resolved_end = _resolve_groupal_period(
        base_records,
        period,
        custom_start=custom_start,
        custom_end=custom_end,
    )
    previous_records = _previous_groupal_period(base_records, period, resolved_start, resolved_end)
    table_context = _build_groupal_table_context(
        period_records,
        estado_selected=registro_estado,
        severidad_selected=registro_severidad,
    )

    return {
        "competitions": competitions,
        "plantel": selected_plantel,
        "period": period,
        "period_label": period_label,
        "active_tab": active_tab,
        "start_date": resolved_start,
        "end_date": resolved_end,
        "custom_start": custom_start or resolved_start,
        "custom_end": custom_end or resolved_end,
        "period_options": [
            {"key": "semana", "label": "Semana"},
            {"key": "mes", "label": "Mes"},
            {"key": "ultimos_3_meses", "label": "Ultimos 3 meses"},
            {"key": "ultimos_6_meses", "label": "Ultimos 6 meses"},
            {"key": "temporada", "label": "Temporada"},
            {"key": "todo", "label": "Todo"},
            {"key": "personalizado", "label": "Personalizado"},
        ],
        "filters": {
            "posicion": posicion or "",
            "tipo": tipo or "",
            "posiciones": position_options,
            "tipos": type_options,
        },
        "kpis": _build_groupal_kpis(period_records, base_records, previous_records=previous_records),
        "records": table_context["records"],
        "record_filters": table_context["filters"],
        "all_records_count": len(plantel_records),
        "filtered_records_count": len(base_records),
        "period_records_count": len(period_records),
        "evolution_charts": _build_evolution_charts(period_records, resolved_start, resolved_end),
        "distribution_charts": _build_distribution_charts(period_records),
        "impact_charts": _build_impact_charts(
            period_records,
            tipo_selected=impact_tipo,
            zona_selected=impact_zona,
        ),
        "players_scatter": _build_players_scatter(period_records),
        "specific_type_heatmap": _build_specific_type_heatmap(period_records),
        "recurrence_type_heatmap": _build_recurrence_type_heatmap(period_records),
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
