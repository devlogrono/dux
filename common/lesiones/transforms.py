from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any


POSITION_OPTIONS = ["Portera", "Defensa", "Centro", "Delantera"]

MAP_POSICIONES = {
    "POR": "Portera",
    "PO": "Portera",
    "GK": "Portera",
    "PORTERA": "Portera",
    "DEF": "Defensa",
    "DF": "Defensa",
    "DEFENSA": "Defensa",
    "MC": "Centro",
    "MED": "Centro",
    "CENTRO": "Centro",
    "CENTROCAMPISTA": "Centro",
    "MEDIOCAMPISTA": "Centro",
    "DEL": "Delantera",
    "DL": "Delantera",
    "FW": "Delantera",
    "FWD": "Delantera",
    "DC": "Delantera",
    "DELANTERA": "Delantera",
    "ATACANTE": "Delantera",
}


def normalize_position(value: Any) -> str:
    if value is None:
        return ""
    raw_value = str(value).strip()
    if not raw_value:
        return ""
    return MAP_POSICIONES.get(raw_value.upper(), raw_value)


def coerce_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value[:10]).date()
        except ValueError:
            return None
    return None


def to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "si", "sí", "s", "yes"}


def contar_sesiones(evolucion: Any) -> int:
    if not evolucion:
        return 0
    data = evolucion
    if isinstance(evolucion, str):
        try:
            data = json.loads(evolucion)
        except json.JSONDecodeError:
            return 0
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        return len(data.get("sesiones", [])) if isinstance(data.get("sesiones"), list) else 1
    return 0


def normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    item = dict(record)
    nombre = str(item.get("nombre") or "").strip()
    apellido = str(item.get("apellido") or "").strip()
    item["nombre_jugadora"] = f"{nombre} {apellido}".strip().upper()
    item["fecha_lesion"] = coerce_date(item.get("fecha_lesion"))
    item["fecha_hora_registro"] = item.get("fecha_hora_registro")
    item["dias_baja_estimado"] = to_float(item.get("dias_baja_estimado"))
    item["es_recidiva"] = is_truthy(item.get("es_recidiva"))
    item["sesiones"] = contar_sesiones(item.get("evolucion"))

    item["posicion"] = normalize_position(item.get("posicion"))

    estado = item.get("estado_lesion")
    item["estado_lesion"] = str(estado).strip().upper() if estado else ""
    return item


def normalize_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [normalize_record(record) for record in records]
