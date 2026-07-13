from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text

from dux import db
from dux.common.physical.queries import (
    get_physical_competitions,
    get_physical_full_records,
    get_physical_overview_stats,
    get_physical_players,
    get_physical_records,
)
from dux.common.physical.transforms import build_record_antropometrico


def build_physical_index_context(plantel: str | None = None) -> dict[str, Any]:
    """
    Construye el contexto de la pantalla inicial de Physical.
    """
    competitions = get_physical_competitions()
    stats = get_physical_overview_stats(plantel=plantel)
    recent_records = get_physical_records(plantel=plantel, limit=15)
    raw_records = get_physical_full_records(plantel=plantel)
    records = [build_record_antropometrico(record) for record in raw_records]
    alert_summary = _build_inicio_alerts(records)

    selected_competition = None
    if plantel:
        selected_competition = next(
            (c for c in competitions if str(c.get("codigo")) == str(plantel)),
            None,
        )

    return {
        "competitions": competitions,
        "plantel": plantel,
        "selected_competition": selected_competition,
        "stats": stats,
        "recent_records": recent_records,
        "alert_summary": alert_summary,
        "alert_cards": alert_summary["cards"],
        "recent_worsening_rows": alert_summary["worsening_rows"],
    }


def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean(values: list[float | None]) -> float | None:
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _coerce_date_value(value):
    if value is None:
        return None
    if hasattr(value, "date"):
        return value.date()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None
    return value


def _calculate_age(value) -> int | None:
    birth_date = _coerce_date_value(value)
    if birth_date is None:
        return None
    today = datetime.today().date()
    return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))


def normalize_player_photo_url(foto_url=None, foto_url_drive=None) -> str | None:
    def normalize(value) -> str | None:
        if value is None:
            return None
        url = str(value).strip()
        if not url or url.lower() in {"no disponible", "none", "null", "nan", "-"}:
            return None

        drive_match = re.search(r"drive\.google\.com/file/d/([^/]+)", url)
        if drive_match:
            return f"https://drive.google.com/uc?export=view&id={drive_match.group(1)}"

        drive_id_match = re.search(r"[?&]id=([^&]+)", url)
        if "drive.google.com" in url and drive_id_match:
            return f"https://drive.google.com/uc?export=view&id={drive_id_match.group(1)}"

        if re.fullmatch(r"[-\w]{20,}", url):
            return f"https://drive.google.com/uc?export=view&id={url}"

        return url

    direct_url = normalize(foto_url)
    if direct_url:
        return direct_url
    return normalize(foto_url_drive)


def _clean_image_url(value) -> str | None:
    if not value:
        return None
    return normalize_player_photo_url(foto_url=value)


def get_physical_player_photo_sources(identificacion: str) -> dict[str, Any]:
    """
    Lee las URLs de foto de informacion_futbolistas para servirlas desde backend.
    """
    sql = text(
        """
        SELECT foto_url, foto_url_drive
        FROM informacion_futbolistas
        WHERE identificacion = :identificacion
        LIMIT 1;
        """
    )
    row = db.session.execute(sql, {"identificacion": identificacion}).mappings().first()
    return dict(row) if row else {}


def _sort_records(records: list[dict[str, Any]], reverse: bool = True) -> list[dict[str, Any]]:
    return sorted(
        records,
        key=lambda r: (
            _coerce_date_value(r.get("fecha_medicion")) is not None,
            _coerce_date_value(r.get("fecha_medicion")),
            _coerce_date_value(r.get("created_at")) is not None,
            _coerce_date_value(r.get("created_at")),
            r.get("id_isak") or 0,
        ),
        reverse=reverse,
    )


def _latest_records_by_player(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest_by_player: dict[Any, dict[str, Any]] = {}

    for record in _sort_records(records, reverse=True):
        player_id = record.get("identificacion") or record.get("nombre_jugadora")
        if player_id and player_id not in latest_by_player:
            latest_by_player[player_id] = record

    return list(latest_by_player.values())


def _records_by_player(records: list[dict[str, Any]]) -> dict[Any, list[dict[str, Any]]]:
    records_by_player: dict[Any, list[dict[str, Any]]] = {}
    for record in records:
        player_id = record.get("identificacion") or record.get("nombre_jugadora")
        if player_id:
            records_by_player.setdefault(player_id, []).append(record)
    return records_by_player


def _player_options_from_records(
    records: list[dict[str, Any]],
    player_info_by_id: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    player_info_by_id = player_info_by_id or {}
    options = []
    for record in _latest_records_by_player(records):
        player_id = record.get("identificacion") or record.get("nombre_jugadora")
        if not player_id:
            continue
        info = player_info_by_id.get(str(player_id), {})
        options.append(
            {
                "id": str(player_id),
                "nombre": str(info.get("nombre_jugadora") or record.get("nombre_jugadora") or player_id).strip(),
                "plantel": info.get("plantel") or record.get("plantel"),
                "fecha_ultima": record.get("fecha_medicion"),
            }
        )

    return sorted(options, key=lambda item: item["nombre"])


def _count_alert(records: list[dict[str, Any]], metric: str, threshold: float, operator: str) -> int:
    total = 0
    for record in records:
        value = _safe_float(record.get(metric))
        if value is None:
            continue
        if operator == "gt" and value > threshold:
            total += 1
        elif operator == "lt" and value < threshold:
            total += 1
    return total


def _worsening_changes(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, str]:
    changes = {}

    grasa_curr = _safe_float(current.get("ajuste_adiposa_pct"))
    grasa_prev = _safe_float(previous.get("ajuste_adiposa_pct"))
    if grasa_curr is not None and grasa_prev is not None and grasa_curr > grasa_prev:
        changes["grasa"] = f"+{grasa_curr - grasa_prev:.1f}"

    musculo_curr = _safe_float(current.get("ajuste_muscular_pct"))
    musculo_prev = _safe_float(previous.get("ajuste_muscular_pct"))
    if musculo_curr is not None and musculo_prev is not None and musculo_curr < musculo_prev:
        changes["musculo"] = f"{musculo_curr - musculo_prev:.1f}"

    pliegues_curr = _safe_float(current.get("suma_6_pliegues_mm"))
    pliegues_prev = _safe_float(previous.get("suma_6_pliegues_mm"))
    if pliegues_curr is not None and pliegues_prev is not None and pliegues_curr > pliegues_prev:
        changes["pliegues"] = f"+{pliegues_curr - pliegues_prev:.1f}"

    imo_curr = _safe_float(current.get("idx_musculo_oseo"))
    imo_prev = _safe_float(previous.get("idx_musculo_oseo"))
    if imo_curr is not None and imo_prev is not None and imo_curr < imo_prev:
        changes["imo"] = f"{imo_curr - imo_prev:.2f}"

    return changes


def _build_inicio_alerts(records: list[dict[str, Any]]) -> dict[str, Any]:
    latest_records = _latest_records_by_player(records)
    pairs = _player_comparison_pairs(records, "ultima")
    previous_records = [pair["previous"] for pair in pairs]

    alert_defs = [
        {
            "key": "grasa_elevada",
            "title": "Grasa elevada",
            "help": "Jugadoras con porcentaje de grasa superior a 24%.",
            "metric": "ajuste_adiposa_pct",
            "threshold": 24,
            "operator": "gt",
        },
        {
            "key": "musculo_bajo",
            "title": "Musculo bajo",
            "help": "Jugadoras con porcentaje muscular inferior a 40%.",
            "metric": "ajuste_muscular_pct",
            "threshold": 40,
            "operator": "lt",
        },
        {
            "key": "pliegues_elevados",
            "title": "Pliegues elevados",
            "help": "Jugadoras con suma de 6 pliegues superior a 90 mm.",
            "metric": "suma_6_pliegues_mm",
            "threshold": 90,
            "operator": "gt",
        },
        {
            "key": "imo_mejorable",
            "title": "IMO mejorable",
            "help": "Jugadoras con indice musculo / oseo inferior a 3.5.",
            "metric": "idx_musculo_oseo",
            "threshold": 3.5,
            "operator": "lt",
        },
    ]

    cards = []
    for alert in alert_defs:
        current_count = _count_alert(
            latest_records,
            alert["metric"],
            alert["threshold"],
            alert["operator"],
        )
        previous_count = _count_alert(
            previous_records,
            alert["metric"],
            alert["threshold"],
            alert["operator"],
        )
        delta = current_count - previous_count
        cards.append(
            {
                "key": alert["key"],
                "title": alert["title"],
                "value": current_count,
                "delta": delta,
                "help": alert["help"],
                "tone": "danger" if current_count else "success",
            }
        )

    worsening_rows = []
    for pair in pairs:
        changes = _worsening_changes(pair["current"], pair["previous"])
        if not changes:
            continue
        worsening_rows.append(
            {
                "identificacion": pair.get("identificacion"),
                "jugadora": pair.get("nombre_jugadora") or pair.get("identificacion") or "",
                "n_metricas": len(changes),
                "grasa": changes.get("grasa"),
                "musculo": changes.get("musculo"),
                "pliegues": changes.get("pliegues"),
                "imo": changes.get("imo"),
            }
        )

    worsening_rows = sorted(
        worsening_rows,
        key=lambda row: (-row["n_metricas"], str(row["jugadora"])),
    )

    cards.append(
        {
            "key": "empeoramiento_reciente",
            "title": "Empeoramientos",
            "value": len(worsening_rows),
            "delta": None,
            "help": "Jugadoras cuya ultima medicion empeora respecto a la anterior en alguna variable clave.",
            "tone": "danger" if worsening_rows else "success",
        }
    )

    return {
        "cards": cards,
        "worsening_rows": worsening_rows,
        "latest_players": len(latest_records),
        "players_with_previous": len(pairs),
    }


def _records_from_last_months(records: list[dict[str, Any]], months: int = 6) -> list[dict[str, Any]]:
    dated_records = [r for r in records if _coerce_date_value(r.get("fecha_medicion")) is not None]
    if not dated_records:
        return []

    max_date = max(_coerce_date_value(r["fecha_medicion"]) for r in dated_records)
    limit_date = max_date - timedelta(days=months * 30)

    return [r for r in dated_records if _coerce_date_value(r["fecha_medicion"]) >= limit_date]


def _period_records(records: list[dict[str, Any]], periodo: str) -> tuple[list[dict[str, Any]], str]:
    if periodo == "historico":
        return _records_from_last_months(records), "ultimos 6 meses"

    return _latest_records_by_player(records), "ultima medicion"


def _metric_value(records: list[dict[str, Any]], metric: str, periodo: str) -> float | None:
    if periodo != "historico":
        return _mean([_safe_float(r.get(metric)) for r in records])

    values_by_player: dict[Any, list[float]] = {}
    for record in records:
        player_id = record.get("identificacion") or record.get("nombre_jugadora")
        value = _safe_float(record.get(metric))
        if player_id and value is not None:
            values_by_player.setdefault(player_id, []).append(value)

    player_means = [_mean(values) for values in values_by_player.values()]
    return _mean(player_means)


def _metric_delta(records: list[dict[str, Any]], metric: str, periodo: str) -> tuple[list[float], float | None]:
    comparable: list[tuple[float, float]] = []

    records_by_player: dict[Any, list[dict[str, Any]]] = {}
    for record in records:
        player_id = record.get("identificacion") or record.get("nombre_jugadora")
        if player_id:
            records_by_player.setdefault(player_id, []).append(record)

    for player_records in records_by_player.values():
        valid_records = [
            r for r in _sort_records(player_records, reverse=True)
            if _safe_float(r.get(metric)) is not None
        ]

        if periodo == "historico":
            valid_records = _sort_records(valid_records, reverse=False)

        if len(valid_records) < 2:
            continue

        if periodo == "historico":
            previous = _safe_float(valid_records[0].get(metric))
            current = _safe_float(valid_records[-1].get(metric))
        else:
            current = _safe_float(valid_records[0].get(metric))
            previous = _safe_float(valid_records[1].get(metric))

        if previous is not None and current is not None:
            comparable.append((previous, current))

    if not comparable:
        return [], None

    previous_mean = _mean([previous for previous, _ in comparable])
    current_mean = _mean([current for _, current in comparable])

    if previous_mean is None or current_mean is None:
        return [], None

    if previous_mean == 0:
        return [previous_mean, current_mean], 0

    return [previous_mean, current_mean], ((current_mean - previous_mean) / previous_mean) * 100


def _metric_status(metric: str, value: float | None) -> tuple[str, str, str]:
    if value is None:
        return "Sin datos", "secondary", "No hay datos suficientes para valorar esta metrica."

    if metric == "peso_medio":
        return "Descriptivo", "secondary", "Indicador de la masa corporal total media del equipo."

    if metric == "grasa_media":
        if value < 14:
            return "Muy bajo", "warning", "Vigilar disponibilidad energetica y contexto fisiologico."
        if value <= 20:
            return "Adecuado", "success", "Rango funcional para futbol femenino."
        if value <= 24:
            return "Moderado", "warning", "Con margen de optimizacion."
        return "Elevado", "danger", "Por encima del perfil deseado de rendimiento."

    if metric == "musculo_medio":
        if value < 40:
            return "Bajo", "warning", "Nivel de masa muscular mejorable para el alto rendimiento."
        if value <= 45:
            return "Adecuado", "success", "Buen nivel de desarrollo muscular para la categoria."
        return "Excelente", "excellent", "Perfil muscular muy favorable para potencia y proteccion estructural."

    if metric == "imo_medio":
        if value < 3.5:
            return "Bajo", "warning", "Relacion musculo / oseo por desarrollar."
        if value <= 4.2:
            return "Adecuado", "success", "Relacion favorable para el rendimiento."
        return "Excelente", "excellent", "Perfil estructural muy favorable."

    if metric == "pliegues_media":
        if value < 50:
            return "Excelente", "excellent", "Perfil de alta competicion."
        if value <= 70:
            return "Adecuado", "success", "Rango funcional para la mayoria de posiciones."
        if value <= 90:
            return "Moderado", "warning", "Con margen de ajuste nutricional y de carga."
        return "Elevado", "danger", "Fuera del rango objetivo de rendimiento."

    return "Descriptivo", "secondary", ""


def _reference_ranges() -> list[dict[str, Any]]:
    return [
        {
            "key": "grasa",
            "label": "% Grasa",
            "items": [
                {"status": "Muy bajo", "range": "<14%", "class": "warning", "interpretation": "Vigilar disponibilidad energetica y contexto fisiologico."},
                {"status": "Adecuado", "range": "14-20%", "class": "success", "interpretation": "Rango funcional para futbol femenino."},
                {"status": "Moderado", "range": "20-24%", "class": "warning", "interpretation": "Con margen de optimizacion."},
                {"status": "Elevado", "range": ">24%", "class": "danger", "interpretation": "Por encima del perfil deseado de rendimiento."},
            ],
        },
        {
            "key": "musculo",
            "label": "% Muscular",
            "items": [
                {"status": "Bajo", "range": "<40%", "class": "warning", "interpretation": "Nivel de masa muscular mejorable para el alto rendimiento."},
                {"status": "Adecuado", "range": "40-45%", "class": "success", "interpretation": "Buen nivel de desarrollo muscular para la categoria."},
                {"status": "Excelente", "range": ">45%", "class": "excellent", "interpretation": "Perfil muscular muy favorable para potencia y proteccion estructural."},
            ],
        },
        {
            "key": "imo",
            "label": "Indice M/O",
            "items": [
                {"status": "Bajo", "range": "<3.5", "class": "warning", "interpretation": "Relacion musculo / oseo por desarrollar."},
                {"status": "Adecuado", "range": "3.5-4.2", "class": "success", "interpretation": "Relacion favorable para el rendimiento."},
                {"status": "Excelente", "range": ">4.2", "class": "excellent", "interpretation": "Perfil estructural muy favorable."},
            ],
        },
        {
            "key": "pliegues",
            "label": "6 pliegues",
            "items": [
                {"status": "Excelente", "range": "<50 mm", "class": "excellent", "interpretation": "Perfil de alta competicion."},
                {"status": "Adecuado", "range": "50-70 mm", "class": "success", "interpretation": "Rango funcional para la mayoria de posiciones."},
                {"status": "Moderado", "range": "70-90 mm", "class": "warning", "interpretation": "Con margen de ajuste nutricional y de carga."},
                {"status": "Elevado", "range": ">90 mm", "class": "danger", "interpretation": "Fuera del rango objetivo de rendimiento."},
            ],
        },
    ]


def _individual_metric_config() -> list[dict[str, Any]]:
    return [
        {"key": "peso", "source": "peso_bruto_kg", "label": "Peso", "unit": "kg", "decimals": 1},
        {"key": "talla", "source": "talla_corporal_cm", "label": "Talla", "unit": "cm", "decimals": 1},
        {"key": "grasa", "source": "ajuste_adiposa_pct", "label": "% Grasa", "unit": "%", "decimals": 1},
        {"key": "musculo", "source": "ajuste_muscular_pct", "label": "% Muscular", "unit": "%", "decimals": 1},
        {"key": "imo", "source": "idx_musculo_oseo", "label": "Indice M/O", "unit": "", "decimals": 2},
        {"key": "pliegues", "source": "suma_6_pliegues_mm", "label": "Suma 6 pliegues", "unit": "mm", "decimals": 1},
        {"key": "masa_osea", "source": "masa_osea_kg", "label": "Masa osea", "unit": "kg", "decimals": 1},
        {"key": "n_mediciones", "source": None, "label": "N mediciones", "unit": "", "decimals": 0},
    ]


def _metric_delta_pct(current_value: float | None, previous_value: float | None) -> float | None:
    if current_value is None or previous_value is None or previous_value == 0:
        return None
    return ((current_value - previous_value) / previous_value) * 100


def _individual_metric_status(metric_key: str, value: float | None) -> tuple[str, str, str]:
    if metric_key in {"peso", "talla", "masa_osea", "n_mediciones"}:
        if value is None:
            return "Sin datos", "secondary", "No hay datos suficientes para valorar esta metrica."
        return "Descriptivo", "secondary", "Variable descriptiva; interpretar en contexto individual y evolutivo."

    status_map = {
        "grasa": "grasa_media",
        "musculo": "musculo_medio",
        "imo": "imo_medio",
        "pliegues": "pliegues_media",
    }
    return _metric_status(status_map.get(metric_key, metric_key), value)


def _build_individual_kpis(
    latest_record: dict[str, Any] | None,
    previous_record: dict[str, Any] | None,
    total_measurements: int = 0,
) -> list[dict[str, Any]]:
    kpis = []
    for config in _individual_metric_config():
        if config["key"] == "n_mediciones":
            current_value = float(total_measurements)
            previous_value = None
            delta = None
        else:
            current_value = _safe_float(latest_record.get(config["source"])) if latest_record else None
            previous_value = _safe_float(previous_record.get(config["source"])) if previous_record else None
            delta = _metric_delta_pct(current_value, previous_value)
        status, status_class, interpretation = _individual_metric_status(config["key"], current_value)
        kpis.append(
            {
                **config,
                "value": current_value,
                "previous_value": previous_value,
                "delta_pct": delta,
                "status": status,
                "status_class": status_class,
                "interpretation": interpretation,
            }
        )
    return kpis


def _build_individual_interpretation(kpis: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = ["peso", "talla", "masa_osea", "grasa", "musculo", "pliegues", "imo"]
    by_key = {metric["key"]: metric for metric in kpis}
    return [by_key[key] for key in order if key in by_key]


def _change_intensity(delta_pct: float | None) -> str:
    if delta_pct is None:
        return ""
    abs_delta = abs(delta_pct)
    if abs_delta < 2:
        return "ligero"
    if abs_delta < 5:
        return "moderado"
    return "marcado"


def _technical_summary_status(metric_key: str, value: float | None) -> dict[str, Any]:
    status, status_class, _ = _individual_metric_status(metric_key, value)
    return {
        "key": metric_key,
        "status": status.lower(),
        "status_class": status_class,
    }


def _build_individual_technical_summary(
    latest_record: dict[str, Any] | None,
    previous_record: dict[str, Any] | None,
) -> dict[str, Any]:
    if not latest_record:
        return {
            "metrics": {},
            "changes": ["sin comparacion previa suficiente"],
            "change_text": "sin comparacion previa suficiente",
            "has_previous": False,
        }

    metric_sources = {
        "grasa": "ajuste_adiposa_pct",
        "musculo": "ajuste_muscular_pct",
        "imo": "idx_musculo_oseo",
        "pliegues": "suma_6_pliegues_mm",
    }
    metrics = {
        key: _technical_summary_status(key, _safe_float(latest_record.get(source)))
        for key, source in metric_sources.items()
    }

    changes = []
    if previous_record:
        current_grasa = _safe_float(latest_record.get("ajuste_adiposa_pct"))
        previous_grasa = _safe_float(previous_record.get("ajuste_adiposa_pct"))
        delta_grasa = _metric_delta_pct(current_grasa, previous_grasa)
        if delta_grasa is not None:
            intensity = _change_intensity(delta_grasa)
            if current_grasa < previous_grasa:
                changes.append(f"{intensity} mejora en grasa corporal")
            elif current_grasa > previous_grasa:
                changes.append(f"{intensity} empeoramiento en grasa corporal")
            else:
                changes.append("estabilidad en grasa corporal")

        current_muscle = _safe_float(latest_record.get("ajuste_muscular_pct"))
        previous_muscle = _safe_float(previous_record.get("ajuste_muscular_pct"))
        delta_muscle = _metric_delta_pct(current_muscle, previous_muscle)
        if delta_muscle is not None:
            intensity = _change_intensity(delta_muscle)
            if current_muscle > previous_muscle:
                changes.append(f"{intensity} mejora muscular")
            elif current_muscle < previous_muscle:
                changes.append(f"{intensity} descenso muscular")
            else:
                changes.append("estabilidad muscular")

        current_pliegues = _safe_float(latest_record.get("suma_6_pliegues_mm"))
        previous_pliegues = _safe_float(previous_record.get("suma_6_pliegues_mm"))
        delta_pliegues = _metric_delta_pct(current_pliegues, previous_pliegues)
        if delta_pliegues is not None:
            intensity = _change_intensity(delta_pliegues)
            if current_pliegues < previous_pliegues:
                changes.append(f"{intensity} reduccion de pliegues")
            elif current_pliegues > previous_pliegues:
                changes.append(f"{intensity} aumento de pliegues")
            else:
                changes.append("estabilidad en pliegues")

        current_imo = _safe_float(latest_record.get("idx_musculo_oseo"))
        previous_imo = _safe_float(previous_record.get("idx_musculo_oseo"))
        delta_imo = _metric_delta_pct(current_imo, previous_imo)
        if delta_imo is not None:
            intensity = _change_intensity(delta_imo)
            if current_imo > previous_imo:
                changes.append(f"{intensity} mejora del indice musculo-oseo")
            elif current_imo < previous_imo:
                changes.append(f"{intensity} descenso del indice musculo-oseo")
            else:
                changes.append("estabilidad del indice musculo-oseo")

    selected_changes = changes[:3] if changes else ["sin comparacion previa suficiente"]
    return {
        "metrics": metrics,
        "changes": selected_changes,
        "change_text": ", ".join(selected_changes),
        "has_previous": previous_record is not None and bool(changes),
    }


def _format_record_date_label(record: dict[str, Any]) -> str:
    date_value = _coerce_date_value(record.get("fecha_medicion"))
    if date_value is not None:
        return date_value.strftime("%d/%m/%Y")
    return str(record.get("fecha_medicion") or record.get("id_isak") or "")


def _perfil_group_interpretation(group: str | None) -> str:
    if group == "G1":
        return "Perfil favorable, con baja adiposidad subcutanea y buena relacion musculo-osea. Compatible con una composicion corporal eficiente para el rendimiento."
    if group == "G2":
        return "Perfil con buena base muscular, aunque con margen de mejora en adiposidad subcutanea. El foco principal estaria en optimizar la composicion corporal sin comprometer la masa funcional."
    if group == "G3":
        return "Perfil ligero, con baja adiposidad pero menor desarrollo relativo en la relacion musculo-osea. Puede existir margen de mejora estructural y de fuerza."
    if group == "G4":
        return "Perfil menos favorable, con mayor adiposidad subcutanea y menor relacion musculo-osea relativa. El foco estaria en mejorar composicion corporal y desarrollo funcional."
    return "Sin datos suficientes."


def _build_individual_perfil_antropometrico_chart(
    records: list[dict[str, Any]],
    selected_player_id: str | None,
) -> dict[str, Any]:
    chart = _build_perfil_antropometrico_chart(records)
    highlighted = None

    for point in chart["points"]:
        is_highlighted = False
        source_id = point.get("identificacion")
        if selected_player_id and source_id is not None:
            is_highlighted = str(source_id) == str(selected_player_id)
        point["highlighted"] = is_highlighted
        if is_highlighted:
            highlighted = point

    return {
        **chart,
        "highlighted": highlighted,
        "interpretation": _perfil_group_interpretation(highlighted.get("grupo") if highlighted else None),
        "caption": "Perfil antropometrico individual dentro del grupo, destacando la ultima medicion disponible de la jugadora.",
    }


def _trend_sentence(metric: str, delta: float | None, label_override: str | None = None) -> str | None:
    if delta is None:
        return None

    if metric in {"grasa", "musculo"}:
        metric_label = label_override or ("porcentaje graso" if metric == "grasa" else "porcentaje muscular")
        if abs(delta) < 1:
            return f"Estabilidad del {metric_label} ({delta:+.1f} pp)"
        if delta > 0:
            label = "Ligero aumento" if abs(delta) < 2 else "Aumento marcado"
            return f"{label} del {metric_label} ({delta:+.1f} pp)"
        label = "Ligera reduccion" if abs(delta) < 2 else "Reduccion marcada"
        return f"{label} del {metric_label} ({delta:+.1f} pp)"

    if metric == "imo":
        if abs(delta) < 0.05:
            return f"Estabilidad del indice musculo-oseo ({delta:+.2f})"
        if delta > 0:
            label = "Ligero aumento" if abs(delta) < 0.15 else "Aumento marcado"
            return f"{label} del indice musculo-oseo ({delta:+.2f})"
        label = "Ligera reduccion" if abs(delta) < 0.15 else "Reduccion marcada"
        return f"{label} del indice musculo-oseo ({delta:+.2f})"

    if metric in {"pliegues", "pliegue"}:
        threshold_stable = 3 if metric == "pliegues" else 1
        threshold_marked = 8 if metric == "pliegues" else 3
        metric_label = label_override or ("suma de pliegues" if metric == "pliegues" else "pliegue")
        if abs(delta) < threshold_stable:
            return f"Estabilidad de {metric_label} ({delta:+.1f} mm)"
        if delta > 0:
            label = "Ligero aumento" if abs(delta) < threshold_marked else "Aumento marcado"
            return f"{label} de {metric_label} ({delta:+.1f} mm)"
        label = "Ligera reduccion" if abs(delta) < threshold_marked else "Reduccion marcada"
        return f"{label} de {metric_label} ({delta:+.1f} mm)"

    if metric == "ratio":
        if abs(delta) < 0.01:
            return f"Estabilidad del ratio ({delta:+.3f})"
        if delta > 0:
            label = "Ligero aumento" if abs(delta) < 0.03 else "Aumento marcado"
            return f"{label} del ratio ({delta:+.3f})"
        label = "Ligera reduccion" if abs(delta) < 0.03 else "Reduccion marcada"
        return f"{label} del ratio ({delta:+.3f})"

    if abs(delta) < 0.8:
        return f"Estabilidad del peso corporal ({delta:+.1f} kg)"
    if delta > 0:
        label = "Ligero aumento" if abs(delta) < 1.5 else "Aumento marcado"
        return f"{label} del peso corporal ({delta:+.1f} kg)"
    label = "Ligera reduccion" if abs(delta) < 1.5 else "Reduccion marcada"
    return f"{label} del peso corporal ({delta:+.1f} kg)"


def _build_individual_peso_grasa_chart(player_records: list[dict[str, Any]]) -> dict[str, Any]:
    sorted_records = _sort_records(player_records, reverse=False)
    points = []

    for record in sorted_records:
        peso = _safe_float(record.get("peso_bruto_kg"))
        grasa = _safe_float(record.get("ajuste_adiposa_pct"))
        if peso is None and grasa is None:
            continue
        points.append(
            {
                "fecha": _format_record_date_label(record),
                "peso": round(peso, 4) if peso is not None else None,
                "grasa": round(grasa, 4) if grasa is not None else None,
            }
        )

    valid_peso = [point["peso"] for point in points if point["peso"] is not None]
    valid_grasa = [point["grasa"] for point in points if point["grasa"] is not None]
    has_enough_data = len(points) >= 2 and len(valid_peso) >= 2 and len(valid_grasa) >= 2

    trends = {
        "last_vs_previous": [],
        "first_vs_last": [],
    }
    if has_enough_data:
        last = points[-1]
        previous = points[-2]
        first = points[0]
        trends["last_vs_previous"] = [
            sentence for sentence in (
                _trend_sentence("grasa", last["grasa"] - previous["grasa"] if last["grasa"] is not None and previous["grasa"] is not None else None),
                _trend_sentence("peso", last["peso"] - previous["peso"] if last["peso"] is not None and previous["peso"] is not None else None),
            )
            if sentence
        ]
        trends["first_vs_last"] = [
            sentence for sentence in (
                _trend_sentence("grasa", last["grasa"] - first["grasa"] if last["grasa"] is not None and first["grasa"] is not None else None),
                _trend_sentence("peso", last["peso"] - first["peso"] if last["peso"] is not None and first["peso"] is not None else None),
            )
            if sentence
        ]

    weight_range = None
    if valid_peso:
        peso_min = min(valid_peso)
        peso_max = max(valid_peso)
        margin = max(0.5, (peso_max - peso_min) * 0.8)
        weight_range = [round(peso_min - margin, 2), round(peso_max + margin, 2)]

    return {
        "points": points,
        "has_enough_data": has_enough_data,
        "weight_range": weight_range,
        "caption": "Evolucion historica del peso corporal y del porcentaje de grasa. Permite contextualizar si los cambios de peso se acompanan de una mejora o empeoramiento de la composicion corporal.",
        "trends": trends,
    }


def _build_two_point_trends(points: list[dict[str, Any]], metrics: list[dict[str, str]]) -> dict[str, list[str]]:
    trends = {"last_vs_previous": [], "first_vs_last": []}
    if len(points) < 2:
        return trends

    last = points[-1]
    previous = points[-2]
    first = points[0]

    for metric in metrics:
        key = metric["key"]
        trend_type = metric["type"]
        label = metric.get("label")
        if last.get(key) is not None and previous.get(key) is not None:
            sentence = _trend_sentence(trend_type, last[key] - previous[key], label)
            if sentence:
                trends["last_vs_previous"].append(sentence)
        if last.get(key) is not None and first.get(key) is not None:
            sentence = _trend_sentence(trend_type, last[key] - first[key], label)
            if sentence:
                trends["first_vs_last"].append(sentence)

    return trends


def _build_individual_composicion_chart(player_records: list[dict[str, Any]]) -> dict[str, Any]:
    sorted_records = _sort_records(player_records, reverse=False)
    mass_config = [
        ("masa_adiposa_kg", "Masa adiposa", "#EF553B"),
        ("masa_muscular_kg", "Masa muscular", "#636EFA"),
        ("masa_osea_kg", "Masa osea", "#00CC96"),
        ("masa_residual_kg", "Masa residual", "#AB63FA"),
        ("masa_piel_kg", "Masa piel", "#FFA15A"),
    ]
    points = []

    for record in sorted_records:
        point = {
            "fecha": _format_record_date_label(record),
            "grasa_pct": _safe_float(record.get("ajuste_adiposa_pct")),
            "musculo_pct": _safe_float(record.get("ajuste_muscular_pct")),
        }
        has_value = point["grasa_pct"] is not None or point["musculo_pct"] is not None
        for key, _, _ in mass_config:
            point[key] = _safe_float(record.get(key))
            has_value = has_value or point[key] is not None
        if has_value:
            points.append(point)

    has_enough_data = len(points) >= 2 and (
        sum(1 for point in points if point.get("grasa_pct") is not None) >= 2
        or sum(1 for point in points if point.get("musculo_pct") is not None) >= 2
        or any(sum(1 for point in points if point.get(key) is not None) >= 2 for key, _, _ in mass_config)
    )

    return {
        "points": points,
        "mass_series": [
            {"key": key, "label": label, "color": color}
            for key, label, color in mass_config
            if any(point.get(key) is not None for point in points)
        ],
        "has_enough_data": has_enough_data,
        "caption": "Evolucion historica del porcentaje graso y muscular, junto con las masas calculadas disponibles.",
        "trends": _build_two_point_trends(
            points,
            [
                {"key": "grasa_pct", "type": "grasa", "label": "porcentaje graso"},
                {"key": "musculo_pct", "type": "musculo", "label": "porcentaje muscular"},
            ],
        ),
    }


def _build_individual_imo_chart(player_records: list[dict[str, Any]]) -> dict[str, Any]:
    points = []
    for record in _sort_records(player_records, reverse=False):
        value = _safe_float(record.get("idx_musculo_oseo"))
        if value is not None:
            points.append({"fecha": _format_record_date_label(record), "value": round(value, 4)})

    return {
        "points": points,
        "has_enough_data": len(points) >= 2,
        "caption": "Evolucion historica del indice musculo-oseo. Permite seguir la relacion entre desarrollo muscular y componente oseo a lo largo del tiempo.",
        "trends": _build_two_point_trends(points, [{"key": "value", "type": "imo"}]),
    }


def _build_individual_pliegues_chart(player_records: list[dict[str, Any]]) -> dict[str, Any]:
    pliegues_config = [
        ("pliegue_triceps", "Triceps"),
        ("pliegue_subescapular", "Subescapular"),
        ("pliegue_biceps", "Biceps"),
        ("pliegue_cresta_iliaca", "Cresta iliaca"),
        ("pliegue_supraespinal", "Supraespinal"),
        ("pliegue_abdominal", "Abdominal"),
        ("pliegue_muslo_frontal", "Muslo frontal"),
        ("pliegue_pantorrilla_maxima", "Pantorrilla maxima"),
        ("pliegue_antebrazo", "Antebrazo"),
    ]
    points = []

    for record in _sort_records(player_records, reverse=False):
        point = {
            "fecha": _format_record_date_label(record),
            "suma_6_pliegues_mm": _safe_float(record.get("suma_6_pliegues_mm")),
        }
        has_value = point["suma_6_pliegues_mm"] is not None
        for key, _ in pliegues_config:
            point[key] = _safe_float(record.get(key))
            has_value = has_value or point[key] is not None
        if has_value:
            points.append(point)

    pliegues = [
        {"key": key, "label": label}
        for key, label in pliegues_config
        if sum(1 for point in points if point.get(key) is not None) >= 1
    ]
    has_enough_data = (
        sum(1 for point in points if point.get("suma_6_pliegues_mm") is not None) >= 2
        or any(sum(1 for point in points if point.get(item["key"]) is not None) >= 2 for item in pliegues)
    )

    return {
        "points": points,
        "pliegues": pliegues,
        "has_enough_data": has_enough_data,
        "caption": "Evolucion historica de la suma de 6 pliegues y detalle por zona anatomica disponible.",
        "trends": _build_two_point_trends(
            points,
            [{"key": "suma_6_pliegues_mm", "type": "pliegues", "label": "la suma de pliegues"}],
        ),
    }


def _ratio_value(record: dict[str, Any], numerator_key: str, denominator_key: str) -> float | None:
    numerator = _safe_float(record.get(numerator_key))
    denominator = _safe_float(record.get(denominator_key))
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _structural_ratio_config() -> list[dict[str, str]]:
    return [
        {
            "key": "ratio_cintura_cadera",
            "label": "Ratio cintura / cadera",
            "short_label": "Cintura / cadera",
            "numerator": "perimetro_cintura_minima",
            "denominator": "perimetro_cadera_maximo",
            "help": "Relacion entre cintura minima y cadera maxima.",
        },
        {
            "key": "ratio_muslo_pantorrilla",
            "label": "Ratio muslo / pantorrilla",
            "short_label": "Muslo / pantorrilla",
            "numerator": "perimetro_muslo_maximo",
            "denominator": "perimetro_pantorrilla_maxima",
            "help": "Relacion entre perimetro de muslo maximo y pantorrilla maxima.",
        },
        {
            "key": "ratio_envergadura_talla",
            "label": "Ratio envergadura / talla",
            "short_label": "Envergadura / talla",
            "numerator": "envergadura_cm",
            "denominator": "talla_corporal_cm",
            "help": "Relacion entre envergadura y talla corporal.",
        },
        {
            "key": "ratio_tronco_altura",
            "label": "Ratio tronco / altura",
            "short_label": "Tronco / altura",
            "numerator": "talla_sentado_cm",
            "denominator": "talla_corporal_cm",
            "help": "Relacion entre talla sentado y talla corporal.",
        },
    ]


def _build_structural_map(records: list[dict[str, Any]], selected_player_id: str | None) -> dict[str, Any]:
    latest_records = _latest_records_by_player(records)
    raw_points = []

    for record in latest_records:
        x_value = _ratio_value(record, "envergadura_cm", "talla_corporal_cm")
        y_value = _ratio_value(record, "perimetro_muslo_maximo", "perimetro_pantorrilla_maxima")
        if x_value is None or y_value is None:
            continue
        raw_points.append(
            {
                "identificacion": record.get("identificacion"),
                "jugadora": str(record.get("nombre_jugadora") or "").strip(),
                "x": x_value,
                "y": y_value,
            }
        )

    if not raw_points:
        return {"points": [], "highlighted": None, "x_cut": None, "y_cut": None, "x_range": None, "y_range": None}

    x_cut = _mean([point["x"] for point in raw_points]) or 0
    y_cut = _mean([point["y"] for point in raw_points]) or 0
    points = []
    highlighted = None

    for point in raw_points:
        x_value = point["x"]
        y_value = point["y"]
        if x_value >= x_cut and y_value >= y_cut:
            group, color = "G1", "#2ECC71"
        elif x_value < x_cut and y_value >= y_cut:
            group, color = "G2", "#F1C40F"
        elif x_value < x_cut and y_value < y_cut:
            group, color = "G3", "#F39C12"
        else:
            group, color = "G4", "#E74C3C"

        is_highlighted = selected_player_id is not None and str(point.get("identificacion")) == str(selected_player_id)
        chart_point = {
            "identificacion": point.get("identificacion"),
            "jugadora": point["jugadora"],
            "x": round(x_value, 4),
            "y": round(y_value, 4),
            "grupo": group,
            "color": color,
            "highlighted": is_highlighted,
            "label": f"{point['jugadora'].title()} ({x_value:.3f}; {y_value:.3f})",
        }
        points.append(chart_point)
        if is_highlighted:
            highlighted = chart_point

    x_values = [point["x"] for point in points]
    y_values = [point["y"] for point in points]
    return {
        "points": points,
        "highlighted": highlighted,
        "x_cut": round(x_cut, 4),
        "y_cut": round(y_cut, 4),
        "x_range": [round(min(x_values) - 0.02, 4), round(max(x_values) + 0.02, 4)],
        "y_range": [round(min(y_values) - 0.05, 4), round(max(y_values) + 0.05, 4)],
    }


def _structural_group_interpretation(group: str | None) -> str:
    if group == "G1":
        return "Perfil con mayor envergadura relativa y buen desarrollo de tren inferior. Compatible con una estructura favorable para amplitud gestual y produccion de fuerza."
    if group == "G2":
        return "Perfil mas compacto, con predominio relativo del tren inferior. Puede asociarse a buena base de fuerza en una estructura menos longilinea."
    if group == "G3":
        return "Perfil mas ligero, con menor envergadura relativa y menor desarrollo del tren inferior. Puede existir margen de mejora estructural y funcional."
    if group == "G4":
        return "Perfil mas longilineo, con menor desarrollo relativo del tren inferior. El foco puede estar en reforzar la base estructural y la capacidad de fuerza."
    return "Sin datos suficientes para interpretar el perfil estructural."


def _build_structural_interpretation(structural_map: dict[str, Any]) -> dict[str, Any]:
    highlighted = structural_map.get("highlighted")
    if not highlighted:
        return {
            "has_data": False,
            "group": None,
            "color": "#6c757d",
            "ratio_envergadura_talla": None,
            "ratio_muslo_pantorrilla": None,
            "text": _structural_group_interpretation(None),
        }

    group = highlighted.get("grupo")
    return {
        "has_data": True,
        "group": group,
        "color": highlighted.get("color") or "#34495E",
        "ratio_envergadura_talla": highlighted.get("x"),
        "ratio_muslo_pantorrilla": highlighted.get("y"),
        "text": _structural_group_interpretation(group),
    }


def _build_individual_perfil_estructural_chart(
    records: list[dict[str, Any]],
    player_records: list[dict[str, Any]],
    selected_player_id: str | None,
) -> dict[str, Any]:
    sorted_records = _sort_records(player_records, reverse=False)
    ratio_config = _structural_ratio_config()
    points = []

    for record in sorted_records:
        point = {"fecha": _format_record_date_label(record)}
        has_value = False
        for config in ratio_config:
            value = _ratio_value(record, config["numerator"], config["denominator"])
            point[config["key"]] = round(value, 4) if value is not None else None
            has_value = has_value or value is not None
        if has_value:
            points.append(point)

    ratio_series = [
        {
            "key": config["key"],
            "label": config["label"],
            "short_label": config["short_label"],
            "help": config["help"],
        }
        for config in ratio_config
        if any(point.get(config["key"]) is not None for point in points)
    ]

    latest = points[-1] if points else None
    previous = points[-2] if len(points) >= 2 else None
    ratio_cards = []
    for series in ratio_series:
        current_value = latest.get(series["key"]) if latest else None
        previous_value = previous.get(series["key"]) if previous else None
        ratio_cards.append(
            {
                **series,
                "value": current_value,
                "delta": current_value - previous_value if current_value is not None and previous_value is not None else None,
            }
        )

    trends = _build_two_point_trends(
        points,
        [{"key": series["key"], "type": "ratio", "label": series["label"]} for series in ratio_series],
    )
    structural_map = _build_structural_map(records, selected_player_id)
    interpretation = _build_structural_interpretation(structural_map)

    return {
        "points": points,
        "ratios": ratio_series,
        "cards": ratio_cards,
        "map": structural_map,
        "interpretation": interpretation,
        "has_enough_data": bool(ratio_series) or bool(structural_map.get("highlighted")),
        "caption": "Ratios estructurales actuales y evolucion historica de proporciones corporales relevantes para el perfil fisico.",
        "map_caption": "Mapa estructural individual dentro del grupo: eje X envergadura/talla y eje Y muslo/pantorrilla.",
        "trends": trends,
    }


def build_physical_individual_context(
    plantel: str | None = None,
    jugadora: str | None = None,
    periodo: str = "ultima",
) -> dict[str, Any]:
    """
    Contexto read-only de la primera vista individual de Physical.
    """
    if periodo not in {"ultima", "historico"}:
        periodo = "ultima"

    competitions = get_physical_competitions()
    player_info = get_physical_players(plantel=plantel)
    player_info_by_id = {
        str(player.get("identificacion")): player
        for player in player_info
        if player.get("identificacion")
    }
    raw_records = get_physical_full_records(plantel=plantel)
    records = [build_record_antropometrico(record) for record in raw_records]
    players = _player_options_from_records(records, player_info_by_id)

    selected_player_id = str(jugadora) if jugadora else (players[0]["id"] if players else None)
    if selected_player_id and players and selected_player_id not in {player["id"] for player in players}:
        selected_player_id = players[0]["id"]

    records_by_player = _records_by_player(records)
    player_records = records_by_player.get(selected_player_id, []) if selected_player_id else []

    if not player_records and selected_player_id is not None:
        for player_id, candidate_records in records_by_player.items():
            if str(player_id) == selected_player_id:
                player_records = candidate_records
                selected_player_id = str(player_id)
                break

    player_records = _sort_records(player_records, reverse=True)
    latest_record = player_records[0] if player_records else None
    previous_record = player_records[1] if len(player_records) > 1 else None
    period_records = _records_from_last_months(player_records) if periodo == "historico" else ([latest_record] if latest_record else [])

    selected_player = None
    if selected_player_id:
        selected_player = next(
            (player for player in players if player["id"] == str(selected_player_id)),
            None,
        )

    selected_player_info = player_info_by_id.get(str(selected_player_id), {}) if selected_player_id else {}
    if selected_player is None and latest_record:
        selected_player = {
            "id": str(latest_record.get("identificacion") or latest_record.get("nombre_jugadora")),
            "nombre": str(latest_record.get("nombre_jugadora") or "").strip(),
            "plantel": latest_record.get("plantel"),
            "fecha_ultima": latest_record.get("fecha_medicion"),
        }

    if selected_player:
        player_photo_url = normalize_player_photo_url(
            foto_url=selected_player_info.get("foto_url"),
            foto_url_drive=selected_player_info.get("foto_url_drive"),
        )
        selected_player = {
            **selected_player,
            "dorsal": selected_player_info.get("dorsal"),
            "nacionalidad": selected_player_info.get("nacionalidad"),
            "posicion": selected_player_info.get("posicion"),
            "fecha_nacimiento": selected_player_info.get("fecha_nacimiento"),
            "edad": _calculate_age(selected_player_info.get("fecha_nacimiento")),
            "foto_url": _clean_image_url(selected_player_info.get("foto_url")),
            "foto_url_drive": _clean_image_url(selected_player_info.get("foto_url_drive")),
            "player_photo_url": player_photo_url,
        }
    else:
        player_photo_url = None

    individual_kpis = _build_individual_kpis(latest_record, previous_record, len(player_records))
    individual_technical_summary = _build_individual_technical_summary(latest_record, previous_record)
    individual_charts = {
        "perfil_antropometrico": _build_individual_perfil_antropometrico_chart(records, selected_player_id),
        "peso_grasa": _build_individual_peso_grasa_chart(player_records),
        "composicion_corporal": _build_individual_composicion_chart(player_records),
        "indice_musculo_oseo": _build_individual_imo_chart(player_records),
        "pliegues": _build_individual_pliegues_chart(player_records),
        "perfil_estructural": _build_individual_perfil_estructural_chart(records, player_records, selected_player_id),
    }

    return {
        "competitions": competitions,
        "plantel": plantel,
        "periodo": periodo,
        "period_label": "ultimos 6 meses" if periodo == "historico" else "ultima medicion",
        "players": players,
        "selected_player_id": selected_player_id,
        "selected_player": selected_player,
        "player_photo_url": player_photo_url,
        "player_records": player_records,
        "period_records": period_records,
        "latest_record": latest_record,
        "previous_record": previous_record,
        "individual_kpis": individual_kpis,
        "individual_technical_summary": individual_technical_summary,
        "individual_charts": individual_charts,
        "interpretation_rows": _build_individual_interpretation(individual_kpis),
        "reference_ranges": _reference_ranges(),
    }


def _registro_field_value(record: dict[str, Any] | None, field: dict[str, Any]):
    if field["name"] == "metodo":
        return "ISAK"
    if field["name"] == "fecha_medicion":
        return datetime.today().date().isoformat()
    if not record:
        return None
    value = record.get(field["name"])
    if value is None:
        return None
    if field.get("type") == "date":
        date_value = _coerce_date_value(value)
        return date_value.isoformat() if date_value else None
    return value


def _physical_registro_form_sections(
    baseline_record: dict[str, Any] | None = None,
    modo_registro: str = "COMPLETO",
) -> list[dict[str, Any]]:
    seguimiento_locked_fields = {
        "talla_corporal_cm",
        "talla_sentado_cm",
        "envergadura_cm",
        "acromial_radial",
        "radial_estiloidea",
        "medial_estiloidea_dactilar",
        "ilioespinal",
        "trocanterea",
        "troc_tibial_lateral",
        "tibial_lateral",
        "tibial_medial_maleolar_medial",
        "pie",
        "biacromial",
        "torax_transverso",
        "torax_antero_posterior",
        "bi_iliocrestideo",
        "humeral_biepicondilar",
        "femoral_biepicondilar",
        "muneca_biestiloideo",
        "tobillo_bimaleolar",
        "mano",
        "perimetro_cabeza",
    }

    sections = [
        {
            "title": "Datos basicos",
            "help": "Informacion general de la medicion ISAK.",
            "fields": [
                {"name": "fecha_medicion", "label": "Fecha de medicion", "type": "date", "unit": ""},
                {"name": "metodo", "label": "Metodo de evaluacion", "type": "text", "unit": "", "disabled_always": True},
                {"name": "peso_bruto_kg", "label": "Peso bruto", "type": "number", "unit": "kg"},
                {"name": "talla_corporal_cm", "label": "Talla corporal", "type": "number", "unit": "cm"},
                {"name": "talla_sentado_cm", "label": "Talla sentado", "type": "number", "unit": "cm"},
                {"name": "envergadura_cm", "label": "Envergadura", "type": "number", "unit": "cm"},
            ],
        },
        {
            "title": "Pliegues",
            "help": "Mediciones de pliegues cutaneos en milimetros.",
            "fields": [
                {"name": "pliegue_triceps", "label": "Triceps", "type": "number", "unit": "mm"},
                {"name": "pliegue_subescapular", "label": "Subescapular", "type": "number", "unit": "mm"},
                {"name": "pliegue_biceps", "label": "Biceps", "type": "number", "unit": "mm"},
                {"name": "pliegue_cresta_iliaca", "label": "Cresta iliaca", "type": "number", "unit": "mm"},
                {"name": "pliegue_supraespinal", "label": "Supraespinal", "type": "number", "unit": "mm"},
                {"name": "pliegue_abdominal", "label": "Abdominal", "type": "number", "unit": "mm"},
                {"name": "pliegue_muslo_frontal", "label": "Muslo frontal", "type": "number", "unit": "mm"},
                {"name": "pliegue_pantorrilla_maxima", "label": "Pantorrilla maxima", "type": "number", "unit": "mm"},
                {"name": "pliegue_antebrazo", "label": "Antebrazo", "type": "number", "unit": "mm"},
            ],
        },
        {
            "title": "Perimetros",
            "help": "Perimetros corporales registrados en centimetros.",
            "fields": [
                {"name": "perimetro_cabeza", "label": "Cabeza", "type": "number", "unit": "cm"},
                {"name": "perimetro_cuello", "label": "Cuello", "type": "number", "unit": "cm"},
                {"name": "perimetro_brazo_relajado", "label": "Brazo relajado", "type": "number", "unit": "cm"},
                {"name": "perimetro_brazo_flexionado_en_tension", "label": "Brazo flexionado en tension", "type": "number", "unit": "cm"},
                {"name": "perimetro_antebrazo_maximo", "label": "Antebrazo maximo", "type": "number", "unit": "cm"},
                {"name": "perimetro_muneca", "label": "Muneca", "type": "number", "unit": "cm"},
                {"name": "perimetro_torax_mesoesternal", "label": "Torax mesoesternal", "type": "number", "unit": "cm"},
                {"name": "perimetro_cintura_minima", "label": "Cintura minima", "type": "number", "unit": "cm"},
                {"name": "perimetro_abdominal_maxima", "label": "Abdominal maxima", "type": "number", "unit": "cm"},
                {"name": "perimetro_cadera_maximo", "label": "Cadera maxima", "type": "number", "unit": "cm"},
                {"name": "perimetro_muslo_maximo", "label": "Muslo maximo", "type": "number", "unit": "cm"},
                {"name": "perimetro_muslo_medial", "label": "Muslo medial", "type": "number", "unit": "cm"},
                {"name": "perimetro_pantorrilla_maxima", "label": "Pantorrilla maxima", "type": "number", "unit": "cm"},
                {"name": "perimetro_tobillo_minima", "label": "Tobillo minima", "type": "number", "unit": "cm"},
            ],
        },
        {
            "title": "Diametros",
            "help": "Diametros oseos y anchos corporales.",
            "fields": [
                {"name": "biacromial", "label": "Biacromial", "type": "number", "unit": "cm"},
                {"name": "torax_transverso", "label": "Torax transverso", "type": "number", "unit": "cm"},
                {"name": "torax_antero_posterior", "label": "Torax antero-posterior", "type": "number", "unit": "cm"},
                {"name": "bi_iliocrestideo", "label": "Bi-iliocrestideo", "type": "number", "unit": "cm"},
                {"name": "humeral_biepicondilar", "label": "Humeral biepicondilar", "type": "number", "unit": "cm"},
                {"name": "femoral_biepicondilar", "label": "Femoral biepicondilar", "type": "number", "unit": "cm"},
                {"name": "muneca_biestiloideo", "label": "Muneca biestiloideo", "type": "number", "unit": "cm"},
                {"name": "tobillo_bimaleolar", "label": "Tobillo bimaleolar", "type": "number", "unit": "cm"},
                {"name": "mano", "label": "Mano", "type": "number", "unit": "cm"},
            ],
        },
        {
            "title": "Longitudes",
            "help": "Longitudes segmentarias disponibles en el registro original.",
            "fields": [
                {"name": "acromial_radial", "label": "Acromial-radial", "type": "number", "unit": "cm"},
                {"name": "radial_estiloidea", "label": "Radial-estiloidea", "type": "number", "unit": "cm"},
                {"name": "medial_estiloidea_dactilar", "label": "Medial-estiloidea-dactilar", "type": "number", "unit": "cm"},
                {"name": "ilioespinal", "label": "Ilioespinal", "type": "number", "unit": "cm"},
                {"name": "trocanterea", "label": "Trocanterea", "type": "number", "unit": "cm"},
                {"name": "troc_tibial_lateral", "label": "Troc-tibial lateral", "type": "number", "unit": "cm"},
                {"name": "tibial_lateral", "label": "Tibial lateral", "type": "number", "unit": "cm"},
                {"name": "tibial_medial_maleolar_medial", "label": "Tibial medial-maleolar medial", "type": "number", "unit": "cm"},
                {"name": "pie", "label": "Pie", "type": "number", "unit": "cm"},
            ],
        },
        {
            "title": "Observaciones",
            "help": "Notas cualitativas de la sesion. Pendiente de conexion a guardado.",
            "fields": [
                {"name": "observaciones", "label": "Observaciones", "type": "textarea", "unit": ""},
            ],
        },
    ]

    is_seguimiento = modo_registro == "SEGUIMIENTO"
    for section in sections:
        for field in section["fields"]:
            field["value"] = _registro_field_value(baseline_record, field)
            field["disabled"] = bool(field.get("disabled_always")) or (
                is_seguimiento and field["name"] in seguimiento_locked_fields
            )
            field["lock_reason"] = "Precargado desde el ultimo ISAK" if field["disabled"] and is_seguimiento else ""

    return sections


def build_physical_registro_context(
    plantel: str | None = None,
    jugadora: str | None = None,
    posicion: str | None = None,
    tipo_registro: str = "formulario",
) -> dict[str, Any]:
    """
    Contexto GET read-only para la primera pantalla de registro Physical.
    """
    tipo_registro = str(tipo_registro or "formulario").lower()
    if tipo_registro not in {"formulario", "archivo"}:
        tipo_registro = "formulario"

    competitions = get_physical_competitions()
    player_info = get_physical_players(plantel=plantel)
    position_options = sorted(
        {
            str(player.get("posicion")).strip()
            for player in player_info
            if player.get("posicion") and str(player.get("posicion")).strip()
        }
    )
    if posicion and posicion not in position_options:
        posicion = None

    if posicion:
        player_info = [
            player
            for player in player_info
            if str(player.get("posicion") or "").strip() == posicion
        ]

    player_info_by_id = {
        str(player.get("identificacion")): player
        for player in player_info
        if player.get("identificacion")
    }
    filtered_player_ids = set(player_info_by_id)

    raw_records = get_physical_full_records(plantel=plantel)
    if posicion:
        raw_records = [
            record
            for record in raw_records
            if str(record.get("identificacion")) in filtered_player_ids
        ]
    records = [build_record_antropometrico(record) for record in raw_records]
    players = _player_options_from_records(records, player_info_by_id)

    if not players:
        players = [
            {
                "id": str(player.get("identificacion")),
                "nombre": str(player.get("nombre_jugadora") or "").strip(),
                "plantel": player.get("plantel"),
                "fecha_ultima": None,
            }
            for player in player_info
            if player.get("identificacion")
        ]

    selected_player_id = str(jugadora) if jugadora else (players[0]["id"] if players else None)
    if selected_player_id and players and selected_player_id not in {player["id"] for player in players}:
        selected_player_id = players[0]["id"]

    records_by_player = _records_by_player(records)
    player_records = _sort_records(records_by_player.get(selected_player_id, []), reverse=True) if selected_player_id else []
    latest_record = player_records[0] if player_records else None
    modo_registro = "SEGUIMIENTO" if latest_record else "COMPLETO"

    selected_player = next(
        (player for player in players if player["id"] == str(selected_player_id)),
        None,
    ) if selected_player_id else None

    selected_player_info = player_info_by_id.get(str(selected_player_id), {}) if selected_player_id else {}
    if selected_player:
        selected_player = {
            **selected_player,
            "dorsal": selected_player_info.get("dorsal"),
            "nacionalidad": selected_player_info.get("nacionalidad"),
            "posicion": selected_player_info.get("posicion"),
            "fecha_nacimiento": selected_player_info.get("fecha_nacimiento"),
            "edad": _calculate_age(selected_player_info.get("fecha_nacimiento")),
        }

    form_sections = _physical_registro_form_sections(
        baseline_record=latest_record,
        modo_registro=modo_registro,
    )

    return {
        "competitions": competitions,
        "plantel": plantel,
        "posicion": posicion,
        "position_options": position_options,
        "tipo_registro": tipo_registro,
        "players": players,
        "selected_player_id": selected_player_id,
        "selected_player": selected_player,
        "latest_record": latest_record,
        "modo_registro": modo_registro,
        "modo_registro_label": "Registro ISAK de seguimiento" if modo_registro == "SEGUIMIENTO" else "Nuevo registro ISAK completo",
        "player_records": player_records,
        "form_sections": form_sections,
        "form_field_count": sum(len(section["fields"]) for section in form_sections),
        "is_read_only": True,
    }


def _build_group_metrics(
    records: list[dict[str, Any]],
    all_records: list[dict[str, Any]],
    periodo: str,
) -> list[dict[str, Any]]:
    metric_config = [
        ("peso_medio", "peso_bruto_kg", "Peso medio del grupo", "kg", 1),
        ("grasa_media", "ajuste_adiposa_pct", "Porcentaje de grasa medio", "%", 1),
        ("musculo_medio", "ajuste_muscular_pct", "Porcentaje muscular medio", "%", 1),
        ("imo_medio", "idx_musculo_oseo", "Indice musculo / oseo medio", "", 2),
        ("pliegues_media", "suma_6_pliegues_mm", "Suma 6 pliegues", "mm", 1),
    ]

    metrics = []
    delta_base = records if periodo == "historico" else all_records

    for key, source, label, unit, decimals in metric_config:
        value = _metric_value(records, source, periodo)
        trend, delta = _metric_delta(delta_base, source, periodo)
        status, status_class, interpretation = _metric_status(key, value)
        metrics.append(
            {
                "key": key,
                "source": source,
                "label": label,
                "value": value,
                "unit": unit,
                "decimals": decimals,
                "delta": delta,
                "trend": trend,
                "status": status,
                "status_class": status_class,
                "interpretation": interpretation,
            }
        )

    return metrics


def _build_technical_summary(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    return {metric["key"]: metric for metric in metrics}


def _perfil_cuadrante(x_value: float, y_value: float) -> tuple[str, str]:
    if x_value <= 70 and y_value >= 3.80:
        return "G1", "#2ECC71"
    if x_value > 70 and y_value >= 3.80:
        return "G2", "#F1C40F"
    if x_value <= 70 and y_value < 3.80:
        return "G3", "#F39C12"
    return "G4", "#E74C3C"


def _build_perfil_antropometrico_chart(records: list[dict[str, Any]]) -> dict[str, Any]:
    plot_records = _latest_records_by_player(records)
    points = []

    for record in plot_records:
        x_value = _safe_float(record.get("suma_6_pliegues_mm"))
        y_value = _safe_float(record.get("idx_musculo_oseo"))
        if x_value is None or y_value is None:
            continue

        group, color = _perfil_cuadrante(x_value, y_value)
        player_name = str(record.get("nombre_jugadora") or "").strip()
        points.append(
            {
                "identificacion": record.get("identificacion"),
                "jugadora": player_name,
                "x": round(x_value, 2),
                "y": round(y_value, 4),
                "grupo": group,
                "color": color,
                "label": f"{player_name.title()} ({x_value:.1f}; {y_value:.2f})",
            }
        )

    counts = {group: 0 for group in ("G1", "G2", "G3", "G4")}
    for point in points:
        counts[point["grupo"]] += 1

    y_values = [point["y"] for point in points]
    y_min = min(3.2, min(y_values) - 0.1) if y_values else 3.2
    y_max = max(4.5, max(y_values) + 0.1) if y_values else 4.5

    return {
        "points": points,
        "counts": counts,
        "x_cut": 70,
        "y_cut": 3.80,
        "x_range": [30, 150],
        "y_range": [round(y_min, 2), round(y_max, 2)],
    }


def _distribucion_metric_config() -> dict[str, dict[str, Any]]:
    return {
        "peso_bruto_kg": {"label": "Peso (kg)", "descriptiva": True},
        "talla_corporal_cm": {"label": "Talla (cm)", "descriptiva": True},
        "suma_6_pliegues_mm": {"label": "Suma 6 pliegues (mm)", "descriptiva": False},
        "ajuste_adiposa_pct": {"label": "% Grasa", "descriptiva": False},
        "ajuste_muscular_pct": {"label": "% Muscular", "descriptiva": False},
        "masa_osea_kg": {"label": "Masa osea (kg)", "descriptiva": True},
        "idx_musculo_oseo": {"label": "Indice musculo-oseo", "descriptiva": False},
    }


def _distribution_color(metric: str, value: float) -> str:
    if metric == "ajuste_adiposa_pct":
        if value < 14:
            return "#F1C40F"
        if value <= 20:
            return "#2ECC71"
        if value <= 24:
            return "#F39C12"
        return "#E74C3C"

    if metric == "ajuste_muscular_pct":
        if value < 40:
            return "#F39C12"
        if value <= 45:
            return "#2ECC71"
        return "#27AE60"

    if metric == "idx_musculo_oseo":
        if value < 3.5:
            return "#F39C12"
        if value <= 4.2:
            return "#2ECC71"
        return "#27AE60"

    if metric == "suma_6_pliegues_mm":
        if value < 50:
            return "#27AE60"
        if value <= 70:
            return "#2ECC71"
        if value <= 90:
            return "#F39C12"
        return "#E74C3C"

    return "#4A6FBF"


def _distribution_reference(metric: str) -> dict[str, Any] | None:
    references = {
        "ajuste_adiposa_pct": {"value": 20, "label": "Limite adecuado"},
        "ajuste_muscular_pct": {"value": 40, "label": "Referencia minima"},
        "idx_musculo_oseo": {"value": 3.5, "label": "Referencia minima"},
        "suma_6_pliegues_mm": {"value": 70, "label": "Limite adecuado"},
    }
    return references.get(metric)


def _distribution_buckets(metric: str, values: list[float]) -> list[dict[str, Any]]:
    bucket_defs = {
        "ajuste_adiposa_pct": [
            ("Muy bajo", "#F1C40F", lambda v: v < 14),
            ("Adecuado", "#2ECC71", lambda v: 14 <= v <= 20),
            ("Moderado", "#F39C12", lambda v: 20 < v <= 24),
            ("Elevado", "#E74C3C", lambda v: v > 24),
        ],
        "ajuste_muscular_pct": [
            ("Bajo", "#F39C12", lambda v: v < 40),
            ("Adecuado", "#2ECC71", lambda v: 40 <= v <= 45),
            ("Excelente", "#27AE60", lambda v: v > 45),
        ],
        "idx_musculo_oseo": [
            ("Bajo", "#F39C12", lambda v: v < 3.5),
            ("Adecuado", "#2ECC71", lambda v: 3.5 <= v <= 4.2),
            ("Excelente", "#27AE60", lambda v: v > 4.2),
        ],
        "suma_6_pliegues_mm": [
            ("Excelente", "#27AE60", lambda v: v < 50),
            ("Adecuado", "#2ECC71", lambda v: 50 <= v <= 70),
            ("Moderado", "#F39C12", lambda v: 70 < v <= 90),
            ("Elevado", "#E74C3C", lambda v: v > 90),
        ],
    }

    return [
        {"label": label, "color": color, "count": sum(1 for value in values if check(value))}
        for label, color, check in bucket_defs.get(metric, [])
    ]


def _build_distribucion_corporal_chart(records: list[dict[str, Any]]) -> dict[str, Any]:
    plot_records = _latest_records_by_player(records)
    metrics = {}

    for metric, config in _distribucion_metric_config().items():
        points = []
        for record in plot_records:
            value = _safe_float(record.get(metric))
            if value is None:
                continue

            points.append(
                {
                    "jugadora": str(record.get("nombre_jugadora") or "").strip(),
                    "value": round(value, 4),
                    "color": _distribution_color(metric, value),
                }
            )

        if not points:
            continue

        points = sorted(points, key=lambda item: item["value"])
        values = [point["value"] for point in points]
        mean_value = _mean(values)

        metrics[metric] = {
            "key": metric,
            "label": config["label"],
            "descriptiva": config["descriptiva"],
            "caption": (
                "Distribucion individual del grupo con valores minimo, maximo y promedio."
                if config["descriptiva"]
                else "Distribucion individual del grupo respecto a rangos de referencia y valores extremos."
            ),
            "points": points,
            "summary": {
                "mean": mean_value,
                "min": min(values),
                "max": max(values),
            },
            "reference": _distribution_reference(metric),
            "buckets": _distribution_buckets(metric, values),
        }

    default_metric = next(iter(metrics), None)
    return {
        "default_metric": default_metric,
        "metrics": metrics,
    }


def _summary_cell_class(column_key: str, value: float | None) -> str:
    if value is None:
        return ""

    if column_key == "grasa_media":
        if value < 14:
            return "cell-warning-soft"
        if value <= 20:
            return "cell-success"
        if value <= 24:
            return "cell-warning"
        return "cell-danger"

    if column_key == "musculo_media":
        if value < 40:
            return "cell-danger"
        if value <= 45:
            return "cell-success"
        return "cell-success-strong"

    if column_key == "pliegues_media":
        if value < 50:
            return "cell-success-strong"
        if value <= 70:
            return "cell-success"
        if value <= 90:
            return "cell-warning"
        return "cell-danger"

    if column_key == "imo_media":
        if value < 3.5:
            return "cell-warning"
        if value <= 4.2:
            return "cell-success"
        return "cell-success-strong"

    return ""


def _build_resumen_grupal_table(records: list[dict[str, Any]]) -> dict[str, Any]:
    records_by_player: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        player_name = str(record.get("nombre_jugadora") or "").strip()
        if player_name:
            records_by_player.setdefault(player_name, []).append(record)

    columns = [
        {"key": "jugadora", "label": "Jugadora", "decimals": None},
        {"key": "peso_medio", "label": "Peso medio (kg)", "decimals": 2},
        {"key": "grasa_media", "label": "% Grasa media", "decimals": 2},
        {"key": "musculo_media", "label": "% Muscular medio", "decimals": 2},
        {"key": "pliegues_media", "label": "6 Pliegues medios (mm)", "decimals": 2},
        {"key": "imo_media", "label": "Indice M/O medio", "decimals": 2},
        {"key": "n_mediciones", "label": "N mediciones", "decimals": 0},
    ]
    rows = []

    for player_name, player_records in records_by_player.items():
        row = {
            "jugadora": player_name,
            "peso_medio": _mean([_safe_float(r.get("peso_bruto_kg")) for r in player_records]),
            "grasa_media": _mean([_safe_float(r.get("ajuste_adiposa_pct")) for r in player_records]),
            "musculo_media": _mean([_safe_float(r.get("ajuste_muscular_pct")) for r in player_records]),
            "pliegues_media": _mean([_safe_float(r.get("suma_6_pliegues_mm")) for r in player_records]),
            "imo_media": _mean([_safe_float(r.get("idx_musculo_oseo")) for r in player_records]),
            "n_mediciones": len(player_records),
        }
        row["cells"] = {
            column["key"]: _summary_cell_class(column["key"], row.get(column["key"]))
            for column in columns
        }
        rows.append(row)

    rows = sorted(
        rows,
        key=lambda row: (
            row["peso_medio"] is None,
            row["peso_medio"] if row["peso_medio"] is not None else 0,
            row["jugadora"],
        ),
    )

    return {
        "columns": columns,
        "rows": rows,
        "sort_options": [
            {"key": "peso_medio", "label": "Peso medio (kg)", "higher_better": None},
            {"key": "grasa_media", "label": "% Grasa media", "higher_better": False},
            {"key": "musculo_media", "label": "% Muscular medio", "higher_better": True},
            {"key": "pliegues_media", "label": "6 Pliegues medios (mm)", "higher_better": False},
            {"key": "imo_media", "label": "Indice M/O medio", "higher_better": True},
            {"key": "n_mediciones", "label": "N mediciones", "higher_better": True},
        ],
    }


def _comparison_metric_config() -> dict[str, dict[str, Any]]:
    return {
        "peso_bruto_kg": {
            "label": "Peso",
            "full_label": "Peso (kg)",
            "positive_good": None,
        },
        "ajuste_adiposa_pct": {
            "label": "Grasa",
            "full_label": "% Grasa",
            "positive_good": False,
        },
        "ajuste_muscular_pct": {
            "label": "Musculo",
            "full_label": "% Muscular",
            "positive_good": True,
        },
        "suma_6_pliegues_mm": {
            "label": "Pliegues",
            "full_label": "Suma 6 pliegues",
            "positive_good": False,
        },
        "idx_musculo_oseo": {
            "label": "IMO",
            "full_label": "Indice musculo-oseo",
            "positive_good": True,
        },
    }


def _delta_color(metric: str, delta: float) -> str:
    positive_good = _comparison_metric_config()[metric]["positive_good"]
    if positive_good is None:
        return "#4A6FBF"
    if delta == 0:
        return "#7F8C8D"
    is_good = delta > 0 if positive_good else delta < 0
    return "#2ECC71" if is_good else "#E74C3C"


def _player_comparison_pairs(records: list[dict[str, Any]], periodo: str) -> list[dict[str, Any]]:
    records_by_player: dict[Any, list[dict[str, Any]]] = {}
    for record in records:
        player_id = record.get("identificacion") or record.get("nombre_jugadora")
        if record.get("fecha_medicion") is not None and player_id:
            records_by_player.setdefault(player_id, []).append(record)

    pairs = []
    for player_records in records_by_player.values():
        sorted_records = _sort_records(player_records, reverse=True)
        if len(sorted_records) < 2:
            continue

        current = sorted_records[0]
        previous = sorted_records[1] if periodo == "ultima" else _sort_records(player_records, reverse=False)[0]

        if current.get("id_isak") == previous.get("id_isak"):
            continue

        pairs.append(
            {
                "identificacion": current.get("identificacion"),
                "nombre_jugadora": current.get("nombre_jugadora") or previous.get("nombre_jugadora"),
                "current": current,
                "previous": previous,
            }
        )

    return pairs


def _metric_delta_from_pairs(pairs: list[dict[str, Any]], metric: str) -> dict[str, Any] | None:
    comparable = []
    for pair in pairs:
        prev_value = _safe_float(pair["previous"].get(metric))
        curr_value = _safe_float(pair["current"].get(metric))
        if prev_value is None or curr_value is None:
            continue
        comparable.append((prev_value, curr_value))

    if not comparable:
        return None

    prev_mean = _mean([prev for prev, _ in comparable])
    curr_mean = _mean([curr for _, curr in comparable])
    if prev_mean is None or curr_mean is None or prev_mean == 0:
        delta_pct = 0
    else:
        delta_pct = ((curr_mean - prev_mean) / prev_mean) * 100

    return {
        "previous_mean": prev_mean,
        "current_mean": curr_mean,
        "delta_pct": delta_pct,
        "n": len(comparable),
    }


def _build_comparacion_mediciones_chart(records: list[dict[str, Any]], periodo: str) -> dict[str, Any]:
    pairs = _player_comparison_pairs(records, periodo)
    metrics = []

    for metric, config in _comparison_metric_config().items():
        delta = _metric_delta_from_pairs(pairs, metric)
        if delta is None:
            continue

        metrics.append(
            {
                "key": metric,
                "label": config["label"],
                "full_label": config["full_label"],
                "delta_pct": round(delta["delta_pct"], 4),
                "previous_mean": delta["previous_mean"],
                "current_mean": delta["current_mean"],
                "n": delta["n"],
                "color": _delta_color(metric, delta["delta_pct"]),
                "positive_good": config["positive_good"],
            }
        )

    values = [metric["delta_pct"] for metric in metrics]
    max_abs = max([abs(value) for value in values], default=1)
    padding = max(max_abs * 0.15, 1)

    return {
        "metrics": metrics,
        "n_players": len(pairs),
        "caption": (
            "La grafica muestra el cambio porcentual promedio del grupo entre la ultima y la medicion anterior."
            if periodo == "ultima"
            else "La grafica muestra el cambio porcentual promedio del grupo entre la primera y la ultima medicion del periodo."
        ),
        "y_range": [round(min(values, default=0) - padding, 2), round(max(values, default=0) + padding, 2)],
        "axis_title": (
            "Cambio (%) respecto a la medicion anterior"
            if periodo == "ultima"
            else "Cambio (%) respecto al inicio del periodo"
        ),
    }


def _build_cambios_clave(records: list[dict[str, Any]], periodo: str) -> dict[str, Any]:
    pairs = _player_comparison_pairs(records, periodo)
    metrics: dict[str, Any] = {}

    for metric, config in _comparison_metric_config().items():
        rows = []
        for pair in pairs:
            prev_value = _safe_float(pair["previous"].get(metric))
            curr_value = _safe_float(pair["current"].get(metric))
            if prev_value is None or curr_value is None or prev_value == 0:
                continue

            delta_pct = ((curr_value - prev_value) / prev_value) * 100
            delta_abs = curr_value - prev_value
            positive_good = config["positive_good"]
            if positive_good is None:
                score = abs(delta_pct)
            else:
                score = delta_pct if positive_good else -delta_pct

            rows.append(
                {
                    "jugadora": str(pair.get("nombre_jugadora") or pair.get("identificacion") or ""),
                    "valor_inicial": round(prev_value, 4),
                    "valor_final": round(curr_value, 4),
                    "delta_pct": round(delta_pct, 4),
                    "delta_abs": round(delta_abs, 4),
                    "score_mejora": round(score, 4),
                }
            )

        if not rows:
            continue

        top_mejora = sorted(rows, key=lambda row: (-row["score_mejora"], row["jugadora"]))
        top_empeora = sorted(rows, key=lambda row: (row["score_mejora"], row["jugadora"]))

        metrics[metric] = {
            "key": metric,
            "label": config["full_label"],
            "positive_good": config["positive_good"],
            "rows": rows,
            "top_mejora": top_mejora[:10],
            "top_empeora": top_empeora[:10],
        }

    default_metric = "ajuste_adiposa_pct" if "ajuste_adiposa_pct" in metrics else next(iter(metrics), None)
    return {
        "caption": "Ranking de jugadoras con mayor mejora y mayor empeoramiento segun la metrica seleccionada.",
        "default_metric": default_metric,
        "metrics": metrics,
    }


def _record_date(record: dict[str, Any]):
    return _coerce_date_value(record.get("fecha_medicion"))


def _build_evolucion_temporal_chart(records: list[dict[str, Any]]) -> dict[str, Any]:
    dated_records = []
    for record in records:
        fecha = _record_date(record)
        if fecha is not None:
            dated_records.append((fecha, record))

    dated_records = sorted(dated_records, key=lambda item: item[0])
    unique_dates = []
    for fecha, _ in dated_records:
        if fecha not in unique_dates:
            unique_dates.append(fecha)

    date_blocks = {}
    current_block = 1
    previous_date = None
    for fecha in unique_dates:
        if previous_date is not None and (fecha - previous_date).days > 7:
            current_block += 1
        date_blocks[fecha] = current_block
        previous_date = fecha

    metric_config = {
        "ajuste_adiposa_pct": {"label": "% Grasa", "reference": {"value": 20, "label": "Limite optimo"}},
        "ajuste_muscular_pct": {"label": "% Muscular", "reference": {"value": 40, "label": "Referencia minima"}},
        "suma_6_pliegues_mm": {"label": "Suma 6 pliegues", "reference": {"value": 70, "label": "Limite adecuado"}},
        "idx_musculo_oseo": {"label": "Indice musculo-oseo", "reference": {"value": 3.5, "label": "Referencia minima"}},
        "peso_bruto_kg": {"label": "Peso", "reference": None},
    }

    metrics = {}
    for metric, config in metric_config.items():
        blocks: dict[int, dict[str, Any]] = {}
        for fecha, record in dated_records:
            value = _safe_float(record.get(metric))
            if value is None:
                continue
            block_id = date_blocks[fecha]
            block = blocks.setdefault(block_id, {"dates": [], "values": []})
            block["dates"].append(fecha)
            block["values"].append(value)

        if len(blocks) < 2:
            continue

        points = []
        for block_id in sorted(blocks):
            block = blocks[block_id]
            values = block["values"]
            start_date = min(block["dates"])
            end_date = max(block["dates"])
            label = (
                start_date.strftime("%d/%m")
                if start_date == end_date
                else f"{start_date.strftime('%d/%m')} - {end_date.strftime('%d/%m')}"
            )
            points.append(
                {
                    "block": block_id,
                    "label": label,
                    "mean": round(_mean(values) or 0, 4),
                    "min": round(min(values), 4),
                    "max": round(max(values), 4),
                    "count": len(values),
                }
            )

        if len(points) < 2:
            continue

        start_mean = points[0]["mean"]
        end_mean = points[-1]["mean"]
        delta_total = ((end_mean - start_mean) / start_mean) * 100 if start_mean else 0
        metrics[metric] = {
            "key": metric,
            "label": config["label"],
            "reference": config["reference"],
            "points": points,
            "summary": {
                "inicio": start_mean,
                "final": end_mean,
                "delta_total": round(delta_total, 4),
                "n_rondas": len(points),
                "n_registros": sum(point["count"] for point in points),
            },
        }

    default_metric = "ajuste_adiposa_pct" if "ajuste_adiposa_pct" in metrics else next(iter(metrics), None)
    return {
        "caption": "Evolucion temporal del promedio grupal por ronda de medicion, con banda de dispersion minima y maxima.",
        "default_metric": default_metric,
        "metrics": metrics,
    }


def _build_perfil_estructural_chart(records: list[dict[str, Any]]) -> dict[str, Any]:
    plot_records = _latest_records_by_player(records)
    raw_points = []

    for record in plot_records:
        envergadura = _safe_float(record.get("envergadura_cm"))
        talla = _safe_float(record.get("talla_corporal_cm"))
        muslo = _safe_float(record.get("perimetro_muslo_maximo"))
        pantorrilla = _safe_float(record.get("perimetro_pantorrilla_maxima"))
        if None in {envergadura, talla, muslo, pantorrilla} or talla == 0 or pantorrilla == 0:
            continue

        raw_points.append(
            {
                "jugadora": str(record.get("nombre_jugadora") or "").strip(),
                "x": envergadura / talla,
                "y": muslo / pantorrilla,
            }
        )

    if not raw_points:
        return {"points": [], "counts": {}, "x_cut": None, "y_cut": None, "x_range": None, "y_range": None}

    x_cut = _mean([point["x"] for point in raw_points]) or 0
    y_cut = _mean([point["y"] for point in raw_points]) or 0
    counts = {group: 0 for group in ("G1", "G2", "G3", "G4")}
    points = []

    for point in raw_points:
        x_value = point["x"]
        y_value = point["y"]
        if x_value >= x_cut and y_value >= y_cut:
            group, color = "G1", "#2ECC71"
        elif x_value < x_cut and y_value >= y_cut:
            group, color = "G2", "#F1C40F"
        elif x_value < x_cut and y_value < y_cut:
            group, color = "G3", "#F39C12"
        else:
            group, color = "G4", "#E74C3C"

        counts[group] += 1
        player_name = point["jugadora"]
        points.append(
            {
                "jugadora": player_name,
                "x": round(x_value, 4),
                "y": round(y_value, 4),
                "grupo": group,
                "color": color,
                "label": f"{player_name.title()} ({x_value:.3f}; {y_value:.3f})",
            }
        )

    x_values = [point["x"] for point in points]
    y_values = [point["y"] for point in points]
    x_min = min(x_values) - 0.02
    x_max = max(x_values) + 0.02
    y_min = min(y_values) - 0.05
    y_max = max(y_values) + 0.05

    return {
        "points": points,
        "counts": counts,
        "x_cut": round(x_cut, 4),
        "y_cut": round(y_cut, 4),
        "x_range": [round(x_min, 4), round(x_max, 4)],
        "y_range": [round(y_min, 4), round(y_max, 4)],
    }


def _build_ratios_estructurales_chart(records: list[dict[str, Any]]) -> dict[str, Any]:
    plot_records = _latest_records_by_player(records)
    ratio_config = {
        "ratio_cintura_cadera": {
            "label": "Ratio cintura / cadera",
            "numerator": "perimetro_cintura_minima",
            "denominator": "perimetro_cadera_maximo",
            "reading": "Valores bajos indican menor cintura relativa respecto a la cadera. Valores altos sugieren mayor predominio central o troncal.",
        },
        "ratio_muslo_pantorrilla": {
            "label": "Ratio muslo / pantorrilla",
            "numerator": "perimetro_muslo_maximo",
            "denominator": "perimetro_pantorrilla_maxima",
            "reading": "Valores altos indican mayor desarrollo relativo del muslo, asociado a produccion de fuerza en tren inferior.",
        },
        "ratio_envergadura_talla": {
            "label": "Ratio envergadura / talla",
            "numerator": "envergadura_cm",
            "denominator": "talla_corporal_cm",
            "reading": "Valores altos indican mayor envergadura relativa, asociada a alcance, cobertura y amplitud gestual.",
        },
        "ratio_tronco_altura": {
            "label": "Ratio tronco / altura",
            "numerator": "talla_sentado_cm",
            "denominator": "talla_corporal_cm",
            "reading": "Valores altos indican mayor proporcion de tronco; valores bajos sugieren mayor longitud relativa de piernas.",
        },
    }

    ratios = {}
    for key, config in ratio_config.items():
        points = []
        for record in plot_records:
            numerator = _safe_float(record.get(config["numerator"]))
            denominator = _safe_float(record.get(config["denominator"]))
            if numerator is None or denominator is None or denominator == 0:
                continue
            points.append(
                {
                    "jugadora": str(record.get("nombre_jugadora") or "").strip(),
                    "value": round(numerator / denominator, 4),
                }
            )

        if not points:
            continue

        points = sorted(points, key=lambda item: item["value"])
        values = [point["value"] for point in points]
        ratios[key] = {
            "key": key,
            "label": config["label"],
            "points": points,
            "summary": {
                "mean": round(_mean(values) or 0, 4),
                "min": min(values),
                "max": max(values),
            },
            "reading": config["reading"],
        }

    default_ratio = "ratio_envergadura_talla" if "ratio_envergadura_talla" in ratios else next(iter(ratios), None)
    return {
        "caption": "Distribucion grupal de ratios estructurales a partir de la ultima medicion disponible de cada jugadora.",
        "default_ratio": default_ratio,
        "ratios": ratios,
    }


def build_physical_grupal_context(plantel: str | None = None, periodo: str = "ultima") -> dict[str, Any]:
    """
    Contexto de la vista grupal read-only.

    Calcula KPIs, deltas e interpretaciones. Despues anadiremos graficos.
    """
    if periodo not in {"ultima", "historico"}:
        periodo = "ultima"

    competitions = get_physical_competitions()
    raw_records = get_physical_full_records(plantel=plantel)
    records = [build_record_antropometrico(record) for record in raw_records]
    period_records, period_label = _period_records(records, periodo)

    total_records = len(records)
    total_players = len({r.get("identificacion") for r in records if r.get("identificacion")})
    period_players = len({r.get("identificacion") for r in period_records if r.get("identificacion")})
    latest_records = _latest_records_by_player(records)
    group_metrics = _build_group_metrics(period_records, records, periodo)
    perfil_antropometrico = _build_perfil_antropometrico_chart(period_records)
    distribucion_corporal = _build_distribucion_corporal_chart(period_records)
    resumen_grupal = _build_resumen_grupal_table(period_records)
    comparison_records = records if periodo == "ultima" else period_records
    comparacion_mediciones = _build_comparacion_mediciones_chart(comparison_records, periodo)
    cambios_clave = _build_cambios_clave(comparison_records, periodo)
    evolucion_temporal = _build_evolucion_temporal_chart(records)
    perfil_estructural = {
        "mapa": _build_perfil_estructural_chart(period_records),
        "ratios": _build_ratios_estructurales_chart(period_records),
    }

    return {
        "competitions": competitions,
        "plantel": plantel,
        "periodo": periodo,
        "period_label": period_label,
        "records": records[:200],
        "latest_records": latest_records,
        "period_records": period_records,
        "group_metrics": group_metrics,
        "grupal_charts": {
            "perfil_antropometrico": perfil_antropometrico,
            "distribucion_corporal": distribucion_corporal,
            "comparacion_mediciones": comparacion_mediciones,
            "evolucion_temporal": evolucion_temporal,
            "perfil_estructural": perfil_estructural,
        },
        "cambios_clave": cambios_clave,
        "resumen_grupal": resumen_grupal,
        "reference_ranges": _reference_ranges(),
        "technical_summary": _build_technical_summary(group_metrics),
        "stats": {
            "total_records": total_records,
            "total_players": total_players,
            "period_records": len(period_records),
            "period_players": period_players,
        },
    }
