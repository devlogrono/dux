from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from flask_login import current_user
from sqlalchemy import text

from dux import db
from dux.common.lesiones.queries import (
    get_lesiones_catalog,
    get_lesiones_competitions,
    get_lesiones_players,
    get_lesiones_records,
)
from dux.common.lesiones.transforms import (
    POSITION_OPTIONS,
    coerce_date,
    normalize_position,
    normalize_records,
)


DEFAULT_PLANTEL = "1FF"
LATERALIDADES = ["NO APLICA", "DERECHA", "IZQUIERDA", "BILATERAL"]
TIPOS_RECIDIVA = ["TEMPRANA (<= 2 MESES)", "TARDIA (2-12 MESES)", "NO APLICA"]
STATUS_OPTIONS = [
    {"key": "todas", "label": "Todas"},
    {"key": "activas", "label": "Activas"},
    {"key": "observacion", "label": "En Observacion"},
    {"key": "inactivas", "label": "Inactivas"},
]
GRAVEDAD_DIAS = [
    {"nombre": "LEVE", "dias_min": 1, "dias_max": 3},
    {"nombre": "MODERADA", "dias_min": 4, "dias_max": 7},
    {"nombre": "GRAVE", "dias_min": 8, "dias_max": 28},
    {"nombre": "MUY GRAVE", "dias_min": 29, "dias_max": None},
]
FOLLOWUP_UPDATE_COLUMNS = [
    "fecha_lesion",
    "lugar_id",
    "segmento_id",
    "zona_cuerpo_id",
    "zona_especifica_id",
    "lateralidad",
    "tipo_lesion_id",
    "tipo_especifico_id",
    "es_recidiva",
    "tipo_recidiva",
    "dias_baja_estimado",
    "impacto_dias_baja_estimado",
    "mecanismo_id",
    "tipo_tratamiento",
    "personal_reporta",
    "fecha_alta_diagnostico",
    "fecha_observacion_activa",
    "fecha_observacion_inactiva",
    "fecha_alta_medica",
    "fecha_alta_deportiva",
    "estado_lesion",
    "diagnostico",
    "descripcion",
    "posicion",
    "evolucion",
]


def _full_name(player: dict[str, Any]) -> str:
    nombre = str(player.get("nombre") or "").strip()
    apellido = str(player.get("apellido") or "").strip()
    return f"{nombre} {apellido}".strip().upper()


def _calculate_age(value: Any) -> int | None:
    birth_date = coerce_date(value)
    if birth_date is None:
        return None
    today = datetime.today().date()
    return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))


def _normalize_players(players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for player in players:
        item = dict(player)
        item["id_jugadora"] = str(item.get("id_jugadora") or "").strip()
        item["nombre_jugadora"] = _full_name(item)
        item["posicion"] = normalize_position(item.get("posicion"))
        item["fecha_nacimiento"] = coerce_date(item.get("fecha_nacimiento"))
        item["edad"] = _calculate_age(item.get("fecha_nacimiento"))
        normalized.append(item)
    return normalized


def _position_options(players: list[dict[str, Any]]) -> list[str]:
    present = {
        str(player.get("posicion") or "").strip()
        for player in players
        if str(player.get("posicion") or "").strip()
    }
    extras = sorted(present - set(POSITION_OPTIONS))
    return [*POSITION_OPTIONS, *extras]


def _select_player(players: list[dict[str, Any]], selected_id: str | None = None) -> dict[str, Any] | None:
    if selected_id:
        for player in players:
            if str(player.get("id_jugadora")) == str(selected_id):
                return player
    return players[0] if players else None


def _catalog_context() -> dict[str, Any]:
    return {
        "segmentos": get_lesiones_catalog("segmentos_corporales"),
        "zonas_segmento": get_lesiones_catalog("zonas_segmento"),
        "zonas_anatomicas": get_lesiones_catalog("zonas_anatomicas"),
        "mecanismos": get_lesiones_catalog("mecanismos"),
        "tipos_lesion": get_lesiones_catalog("tipo_lesion"),
        "tipos_especificos": get_lesiones_catalog("tipo_especifico_lesion"),
        "relaciones": get_lesiones_catalog("mecanismo_tipo_lesion"),
        "tratamientos": get_lesiones_catalog("tratamientos"),
        "lugares": get_lesiones_catalog("lugares"),
    }


def _filter_zonas_segmento(catalogs: dict[str, Any], segmento_id: str | None) -> list[dict[str, Any]]:
    if not segmento_id:
        return []
    return [row for row in catalogs["zonas_segmento"] if str(row.get("segmento_id")) == str(segmento_id)]


def _filter_zonas_anatomicas(catalogs: dict[str, Any], zona_cuerpo_id: str | None) -> list[dict[str, Any]]:
    if not zona_cuerpo_id:
        return []
    return [row for row in catalogs["zonas_anatomicas"] if str(row.get("zona_id")) == str(zona_cuerpo_id)]


def _filter_tipos_lesion(catalogs: dict[str, Any], mecanismo_id: str | None) -> list[dict[str, Any]]:
    if not mecanismo_id:
        return []
    tipo_ids = {
        str(row.get("tipo_lesion_id"))
        for row in catalogs["relaciones"]
        if str(row.get("mecanismo_id")) == str(mecanismo_id)
    }
    return [row for row in catalogs["tipos_lesion"] if str(row.get("id")) in tipo_ids]


def _filter_tipos_especificos(
    catalogs: dict[str, Any],
    mecanismo_id: str | None,
    tipo_lesion_id: str | None,
) -> list[dict[str, Any]]:
    if not mecanismo_id or not tipo_lesion_id:
        return []
    specific_ids = {
        str(row.get("tipo_especifico_id"))
        for row in catalogs["relaciones"]
        if str(row.get("mecanismo_id")) == str(mecanismo_id)
        and str(row.get("tipo_lesion_id")) == str(tipo_lesion_id)
        and row.get("tipo_especifico_id") is not None
    }
    return [row for row in catalogs["tipos_especificos"] if str(row.get("id")) in specific_ids]


def _request_bool(value: Any) -> bool:
    return str(value or "").lower() in {"1", "true", "on", "si", "sí"}


def _arg_list(args: Any, key: str) -> list[str]:
    if hasattr(args, "getlist"):
        return [str(value) for value in args.getlist(key)]
    value = args.get(key)
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _clean_id(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _valid_catalog_id(rows: list[dict[str, Any]], value: Any) -> bool:
    return str(value or "") in {str(row.get("id")) for row in rows}


def _username() -> str:
    return str(
        getattr(current_user, "email", "")
        or getattr(current_user, "username", "")
        or getattr(current_user, "name", "")
        or ""
    )


def _date_iso(value: Any, fallback: date | None = None) -> str:
    parsed = coerce_date(value) or fallback
    return parsed.isoformat() if parsed else ""


def _severity_for_days(days: int | None) -> str | None:
    if days is None or days <= 0:
        return None
    for item in GRAVEDAD_DIAS:
        min_days = item["dias_min"]
        max_days = item["dias_max"]
        if days >= min_days and (max_days is None or days <= max_days):
            return item["nombre"]
    return None


def _parse_json_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if isinstance(parsed, list):
        return parsed
    return []


def _selected_treatment_ids(raw_value: Any, treatments: list[dict[str, Any]]) -> list[str]:
    raw_items = [str(item).strip().upper() for item in _parse_json_list(raw_value) if str(item).strip()]
    ids = []
    for treatment in treatments:
        if str(treatment.get("nombre") or "").strip().upper() in raw_items:
            ids.append(str(treatment.get("id")))
    return ids


def _selected_treatment_names(raw_values: list[str], treatments: list[dict[str, Any]]) -> list[str]:
    valid_by_id = {str(row.get("id")): str(row.get("nombre") or "").upper() for row in treatments}
    return [valid_by_id[value] for value in raw_values if value in valid_by_id]


def _lesiones_columns() -> set[str]:
    rows = db.session.execute(text("SHOW COLUMNS FROM lesiones;")).mappings().all()
    return {str(row.get("Field")) for row in rows}


def _apply_status_filter(records: list[dict[str, Any]], status: str) -> list[dict[str, Any]]:
    if status == "activas":
        return [record for record in records if str(record.get("estado_lesion") or "").upper() == "ACTIVO"]
    if status == "observacion":
        return [record for record in records if str(record.get("estado_lesion") or "").upper() == "OBSERVACION"]
    if status == "inactivas":
        return [record for record in records if str(record.get("estado_lesion") or "").upper() == "INACTIVO"]
    return records


def _sort_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        records,
        key=lambda record: (
            coerce_date(record.get("fecha_lesion")) or date.min,
            record.get("fecha_hora_registro") or datetime.min,
        ),
        reverse=True,
    )


def _record_form(record: dict[str, Any] | None, args: Any, catalogs: dict[str, Any]) -> dict[str, Any]:
    today = date.today()
    submitted = args.get("preview") is not None or args.get("fecha_lesion") is not None
    fecha_lesion = _date_iso(args.get("fecha_lesion") or (record or {}).get("fecha_lesion"), today)
    fecha_alta = _date_iso(
        args.get("fecha_alta_diagnostico") or (record or {}).get("fecha_alta_diagnostico"),
        today + timedelta(days=1),
    )
    days_raw = (record or {}).get("dias_baja_estimado")
    implica_baja_default = bool((days_raw or 0) > 0 or (record or {}).get("fecha_alta_diagnostico"))
    form = {
        "fecha_lesion": fecha_lesion,
        "lugar_id": str(args.get("lugar_id") or (record or {}).get("lugar_id") or ""),
        "mecanismo_id": str(args.get("mecanismo_id") or (record or {}).get("mecanismo_id") or ""),
        "tipo_lesion_id": str(args.get("tipo_lesion_id") or (record or {}).get("tipo_lesion_id") or ""),
        "tipo_especifico_id": str(args.get("tipo_especifico_id") or (record or {}).get("tipo_especifico_id") or ""),
        "segmento_id": str(args.get("segmento_id") or (record or {}).get("segmento_id") or ""),
        "zona_cuerpo_id": str(args.get("zona_cuerpo_id") or (record or {}).get("zona_cuerpo_id") or ""),
        "zona_especifica_id": str(args.get("zona_especifica_id") or (record or {}).get("zona_especifica_id") or ""),
        "lateralidad": args.get("lateralidad") or (record or {}).get("lateralidad") or "NO APLICA",
        "diagnostico": args.get("diagnostico") or (record or {}).get("diagnostico") or "",
        "es_recidiva": _request_bool(args.get("es_recidiva")) if submitted else bool((record or {}).get("es_recidiva")),
        "tipo_recidiva": args.get("tipo_recidiva") or (record or {}).get("tipo_recidiva") or "NO APLICA",
        "implica_baja": _request_bool(args.get("implica_baja")) if submitted else implica_baja_default,
        "fecha_alta_diagnostico": fecha_alta,
        "tipo_tratamiento": _arg_list(args, "tipo_tratamiento") or _selected_treatment_ids((record or {}).get("tipo_tratamiento"), catalogs["tratamientos"]),
        "personal_reporta": args.get("personal_reporta") or (record or {}).get("personal_reporta") or "",
        "descripcion": args.get("descripcion") or (record or {}).get("descripcion") or "",
    }
    return form


def _evolution_form(args: Any, selected_lesion: dict[str, Any] | None) -> dict[str, Any]:
    submitted = args.get("preview") is not None or args.get("action") == "save_followup"
    alta_medica_value = bool((selected_lesion or {}).get("fecha_alta_medica"))
    alta_deportiva_value = bool((selected_lesion or {}).get("fecha_alta_deportiva"))
    return {
        "add_evolution": _request_bool(args.get("add_evolution")) if submitted else False,
        "fecha_control": _date_iso(args.get("fecha_control"), date.today()),
        "tratamiento_aplicado": _arg_list(args, "tratamiento_aplicado"),
        "personal_medico": args.get("personal_medico") or "",
        "observaciones": args.get("observaciones") or "",
        "change_state": _request_bool(args.get("change_state")) if submitted else False,
        "nuevo_estado": args.get("nuevo_estado") or "",
        "alta_medica": _request_bool(args.get("alta_medica")) if submitted else alta_medica_value,
        "fecha_alta_medica": _date_iso(
            args.get("fecha_alta_medica") or (selected_lesion or {}).get("fecha_alta_medica"),
            date.today(),
        ),
        "alta_deportiva": _request_bool(args.get("alta_deportiva")) if submitted else alta_deportiva_value,
        "fecha_alta_deportiva": _date_iso(
            args.get("fecha_alta_deportiva") or (selected_lesion or {}).get("fecha_alta_deportiva"),
            date.today(),
        ),
    }


def append_evolution_entry(
    current_evolution: Any,
    evolution_form: dict[str, Any],
    treatments: list[dict[str, Any]],
) -> list[Any]:
    evolution = _parse_json_list(current_evolution)
    if not evolution_form.get("add_evolution"):
        return evolution

    has_content = any(
        [
            evolution_form.get("tratamiento_aplicado"),
            str(evolution_form.get("personal_medico") or "").strip(),
            str(evolution_form.get("observaciones") or "").strip(),
        ]
    )
    if not has_content:
        return evolution

    evolution.append(
        {
            "fecha_control": evolution_form.get("fecha_control"),
            "tratamiento_aplicado": _selected_treatment_names(
                evolution_form.get("tratamiento_aplicado") or [],
                treatments,
            ),
            "personal_seguimiento": str(evolution_form.get("personal_medico") or "").strip(),
            "observaciones": str(evolution_form.get("observaciones") or "").strip(),
            "fecha_hora_registro": datetime.now().isoformat(timespec="seconds"),
            "usuario": _username(),
        }
    )
    return evolution


def validate_followup_record(
    args: Any,
    selected_lesion: dict[str, Any] | None,
    catalogs: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if not selected_lesion or not selected_lesion.get("id_lesion"):
        errors.append("No se encontro la lesion seleccionada")

    fecha_lesion = coerce_date(args.get("fecha_lesion"))
    fecha_alta = coerce_date(args.get("fecha_alta_diagnostico"))
    fecha_control = coerce_date(args.get("fecha_control"))
    fecha_alta_medica = coerce_date(args.get("fecha_alta_medica"))
    fecha_alta_deportiva = coerce_date(args.get("fecha_alta_deportiva"))
    implica_baja = _request_bool(args.get("implica_baja"))
    add_evolution = _request_bool(args.get("add_evolution"))
    alta_medica = _request_bool(args.get("alta_medica"))
    alta_deportiva = _request_bool(args.get("alta_deportiva"))

    if not fecha_lesion:
        errors.append("Falta: Fecha de lesion valida")

    required = {
        "lugar_id": ("Lugar", catalogs["lugares"]),
        "segmento_id": ("Region anatomica", catalogs["segmentos"]),
        "zona_cuerpo_id": ("Zona anatomica", catalogs["zonas_segmento"]),
        "tipo_lesion_id": ("Tipo de lesion", catalogs["tipos_lesion"]),
        "mecanismo_id": ("Mecanismo de lesion", catalogs["mecanismos"]),
    }
    for key, (label, rows) in required.items():
        value = args.get(key)
        if not value:
            errors.append(f"Falta: {label}")
        elif not _valid_catalog_id(rows, value):
            errors.append(f"Valor no valido: {label}")

    if not str(args.get("personal_reporta") or "").strip():
        errors.append("Falta: Personal medico que reporta")

    segmento_id = str(args.get("segmento_id") or "")
    zona_cuerpo_id = str(args.get("zona_cuerpo_id") or "")
    zona_especifica_id = str(args.get("zona_especifica_id") or "")
    mecanismo_id = str(args.get("mecanismo_id") or "")
    tipo_lesion_id = str(args.get("tipo_lesion_id") or "")
    tipo_especifico_id = str(args.get("tipo_especifico_id") or "")

    if zona_cuerpo_id and zona_cuerpo_id not in {str(row.get("id")) for row in _filter_zonas_segmento(catalogs, segmento_id)}:
        errors.append("La zona anatomica no pertenece a la region seleccionada")
    if zona_especifica_id and zona_especifica_id not in {str(row.get("id")) for row in _filter_zonas_anatomicas(catalogs, zona_cuerpo_id)}:
        errors.append("La estructura anatomica no pertenece a la zona seleccionada")
    if tipo_lesion_id and tipo_lesion_id not in {str(row.get("id")) for row in _filter_tipos_lesion(catalogs, mecanismo_id)}:
        errors.append("El tipo de lesion no corresponde al mecanismo seleccionado")
    if tipo_especifico_id and tipo_especifico_id not in {
        str(row.get("id")) for row in _filter_tipos_especificos(catalogs, mecanismo_id, tipo_lesion_id)
    }:
        errors.append("El tipo especifico no corresponde al mecanismo y tipo seleccionados")

    if implica_baja:
        if not fecha_alta:
            errors.append("Si implica baja, informa alta deportiva estimada")
        elif fecha_lesion and fecha_alta < fecha_lesion:
            errors.append("La fecha de alta estimada no puede ser anterior a la fecha de lesion")

    if add_evolution:
        if not fecha_control:
            errors.append("Falta: Fecha de control valida")
        elif fecha_lesion and fecha_control < fecha_lesion:
            errors.append("La fecha de control no puede ser anterior a la fecha de lesion")
        if not str(args.get("personal_medico") or "").strip():
            errors.append("Falta: Personal medico del seguimiento")

    if alta_medica:
        if not fecha_alta_medica:
            errors.append("Falta: Fecha de alta medica")
        elif fecha_lesion and fecha_alta_medica < fecha_lesion:
            errors.append("La fecha de alta medica no puede ser anterior a la fecha de lesion")

    if alta_deportiva:
        if not fecha_alta_deportiva:
            errors.append("Falta: Fecha de alta deportiva")
        elif fecha_lesion and fecha_alta_deportiva < fecha_lesion:
            errors.append("La fecha de alta deportiva no puede ser anterior a la fecha de lesion")
        elif fecha_alta_medica and fecha_alta_deportiva < fecha_alta_medica:
            errors.append("La fecha de alta deportiva no puede ser anterior al alta medica")

    return errors


def build_followup_update_record(
    args: Any,
    selected_lesion: dict[str, Any],
    catalogs: dict[str, Any],
) -> dict[str, Any]:
    fecha_lesion = coerce_date(args.get("fecha_lesion"))
    fecha_alta = coerce_date(args.get("fecha_alta_diagnostico"))
    fecha_alta_medica = coerce_date(args.get("fecha_alta_medica"))
    fecha_alta_deportiva = coerce_date(args.get("fecha_alta_deportiva"))
    implica_baja = _request_bool(args.get("implica_baja"))
    es_recidiva = _request_bool(args.get("es_recidiva"))
    alta_medica = _request_bool(args.get("alta_medica"))
    alta_deportiva = _request_bool(args.get("alta_deportiva"))
    evolution_form = _evolution_form(args, selected_lesion)

    days = 0
    if implica_baja and fecha_lesion and fecha_alta:
        days = max(0, (fecha_alta - fecha_lesion).days)

    estado = "ACTIVO" if implica_baja else "OBSERVACION"
    fecha_observacion_activa = selected_lesion.get("fecha_observacion_activa")
    fecha_observacion_inactiva = selected_lesion.get("fecha_observacion_inactiva")

    if evolution_form.get("change_state") and evolution_form.get("nuevo_estado"):
        estado = evolution_form["nuevo_estado"]
        fecha_control = coerce_date(evolution_form.get("fecha_control")) or date.today()
        if estado == "ACTIVO" and not fecha_observacion_activa:
            fecha_observacion_activa = fecha_control
        if estado == "INACTIVO" and not alta_deportiva and not fecha_observacion_inactiva:
            fecha_observacion_inactiva = fecha_control

    if alta_deportiva:
        estado = "INACTIVO"

    treatments = _selected_treatment_names(_arg_list(args, "tipo_tratamiento"), catalogs["tratamientos"])
    evolution = append_evolution_entry(selected_lesion.get("evolucion"), evolution_form, catalogs["tratamientos"])
    return {
        "id_lesion": selected_lesion.get("id_lesion"),
        "fecha_lesion": fecha_lesion,
        "lugar_id": _clean_id(args.get("lugar_id")),
        "segmento_id": _clean_id(args.get("segmento_id")),
        "zona_cuerpo_id": _clean_id(args.get("zona_cuerpo_id")),
        "zona_especifica_id": _clean_id(args.get("zona_especifica_id")),
        "lateralidad": args.get("lateralidad") or "NO APLICA",
        "tipo_lesion_id": _clean_id(args.get("tipo_lesion_id")),
        "tipo_especifico_id": _clean_id(args.get("tipo_especifico_id")),
        "es_recidiva": es_recidiva,
        "tipo_recidiva": args.get("tipo_recidiva") if es_recidiva else "NO APLICA",
        "dias_baja_estimado": days,
        "impacto_dias_baja_estimado": _severity_for_days(days),
        "mecanismo_id": _clean_id(args.get("mecanismo_id")),
        "tipo_tratamiento": json.dumps(treatments, ensure_ascii=False),
        "personal_reporta": str(args.get("personal_reporta") or "").strip(),
        "fecha_alta_diagnostico": fecha_alta if implica_baja else None,
        "fecha_observacion_activa": fecha_observacion_activa,
        "fecha_observacion_inactiva": fecha_observacion_inactiva,
        "fecha_alta_medica": fecha_alta_medica if alta_medica else selected_lesion.get("fecha_alta_medica"),
        "fecha_alta_deportiva": fecha_alta_deportiva if alta_deportiva else selected_lesion.get("fecha_alta_deportiva"),
        "estado_lesion": estado,
        "diagnostico": str(args.get("diagnostico") or "").strip(),
        "descripcion": str(args.get("descripcion") or "").strip(),
        "posicion": str(selected_lesion.get("posicion") or "").upper() or None,
        "evolucion": json.dumps(evolution, ensure_ascii=False),
    }


def save_followup_record(record: dict[str, Any]) -> None:
    columns = set(FOLLOWUP_UPDATE_COLUMNS)
    db_columns = _lesiones_columns()
    update_columns = [column for column in FOLLOWUP_UPDATE_COLUMNS if column in columns and column in db_columns]
    set_clause = ", ".join(f"{column} = :{column}" for column in update_columns)
    if "updated_at" in db_columns:
        set_clause = f"{set_clause}, updated_at = CURRENT_TIMESTAMP"
    params = {column: record.get(column) for column in update_columns}
    params["id_lesion"] = record["id_lesion"]
    sql = text(f"UPDATE lesiones SET {set_clause} WHERE id_lesion = :id_lesion;")
    try:
        db.session.execute(sql, params)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def save_lesion_followup(args: Any) -> tuple[bool, str | None, list[str]]:
    selected_plantel = args.get("plantel") or DEFAULT_PLANTEL
    records = normalize_records(get_lesiones_records(selected_plantel))
    selected_lesion = next(
        (record for record in records if str(record.get("id_lesion")) == str(args.get("lesion") or args.get("id_lesion") or "")),
        None,
    )
    catalogs = _catalog_context()
    errors = validate_followup_record(args, selected_lesion, catalogs)
    if errors:
        return False, None, errors

    record = build_followup_update_record(args, selected_lesion, catalogs)
    save_followup_record(record)
    return True, str(record["id_lesion"]), []


def _build_preview_payload(
    selected_lesion: dict[str, Any],
    form: dict[str, Any],
    evolution_form: dict[str, Any],
    catalogs: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    fecha_lesion = coerce_date(form.get("fecha_lesion"))
    fecha_alta = coerce_date(form.get("fecha_alta_diagnostico"))
    days = 0
    if form.get("implica_baja") and fecha_lesion and fecha_alta:
        days = max(0, (fecha_alta - fecha_lesion).days)

    estado = "ACTIVO" if form.get("implica_baja") else "OBSERVACION"
    treatments = _selected_treatment_names(form.get("tipo_tratamiento") or [], catalogs["tratamientos"])
    evolution = _parse_json_list(selected_lesion.get("evolucion"))
    evolution = append_evolution_entry(selected_lesion.get("evolucion"), evolution_form, catalogs["tratamientos"])

    if evolution_form.get("change_state") and evolution_form.get("nuevo_estado"):
        estado = evolution_form["nuevo_estado"]
    if evolution_form.get("alta_deportiva"):
        estado = "INACTIVO"

    payload = {
        "id": selected_lesion.get("id_registro"),
        "id_lesion": selected_lesion.get("id_lesion"),
        "id_jugadora": selected_lesion.get("id_jugadora"),
        "fecha_lesion": form.get("fecha_lesion"),
        "lugar_id": form.get("lugar_id") or None,
        "mecanismo_id": form.get("mecanismo_id") or None,
        "tipo_lesion_id": form.get("tipo_lesion_id") or None,
        "tipo_especifico_id": form.get("tipo_especifico_id") or None,
        "segmento_id": form.get("segmento_id") or None,
        "zona_cuerpo_id": form.get("zona_cuerpo_id") or None,
        "zona_especifica_id": form.get("zona_especifica_id") or None,
        "lateralidad": form.get("lateralidad"),
        "diagnostico": form.get("diagnostico"),
        "es_recidiva": bool(form.get("es_recidiva")),
        "tipo_recidiva": form.get("tipo_recidiva") if form.get("es_recidiva") else "NO APLICA",
        "dias_baja_estimado": days,
        "impacto_dias_baja_estimado": _severity_for_days(days),
        "tipo_tratamiento": treatments,
        "personal_reporta": form.get("personal_reporta"),
        "fecha_alta_diagnostico": form.get("fecha_alta_diagnostico") if form.get("implica_baja") else None,
        "fecha_alta_medica": evolution_form.get("fecha_alta_medica") if evolution_form.get("alta_medica") else selected_lesion.get("fecha_alta_medica"),
        "fecha_alta_deportiva": evolution_form.get("fecha_alta_deportiva") if evolution_form.get("alta_deportiva") else selected_lesion.get("fecha_alta_deportiva"),
        "estado_lesion": estado,
        "descripcion": form.get("descripcion"),
        "evolucion": evolution,
    }
    summary = {
        "dias_baja_estimado": days,
        "estado_lesion": estado,
        "impacto_dias_baja_estimado": _severity_for_days(days) or "-",
        "dry_run": True,
    }
    return payload, summary


def build_lesiones_seguimiento_context(args: Any) -> dict[str, Any]:
    selected_plantel = args.get("plantel") or DEFAULT_PLANTEL
    competitions = get_lesiones_competitions()
    all_players = _normalize_players(get_lesiones_players(selected_plantel))
    position_options = _position_options(all_players)
    selected_position = normalize_position(args.get("posicion")) or None
    if selected_position and selected_position not in position_options:
        selected_position = None
    players = [player for player in all_players if player.get("posicion") == selected_position] if selected_position else all_players
    selected_player = _select_player(players, args.get("jugadora"))

    records = normalize_records(get_lesiones_records(selected_plantel))
    if selected_position:
        records = [record for record in records if record.get("posicion") == selected_position]
    if selected_player:
        records = [record for record in records if str(record.get("id_jugadora")) == str(selected_player.get("id_jugadora"))]

    tipo_options = sorted({str(record.get("tipo_lesion") or "") for record in records if record.get("tipo_lesion")})
    selected_tipo = args.get("tipo") or ""
    if selected_tipo:
        records = [record for record in records if str(record.get("tipo_lesion") or "") == selected_tipo]

    selected_status = args.get("status") or "todas"
    if selected_status not in {option["key"] for option in STATUS_OPTIONS}:
        selected_status = "todas"
    records_before_status = list(records)
    records = _sort_records(_apply_status_filter(records, selected_status))

    selected_lesion_id = args.get("lesion") or (str(records[0].get("id_lesion")) if records else "")
    selected_lesion = next(
        (record for record in records if str(record.get("id_lesion")) == str(selected_lesion_id)),
        None,
    )
    if selected_lesion is None and selected_lesion_id:
        selected_lesion = next(
            (record for record in records_before_status if str(record.get("id_lesion")) == str(selected_lesion_id)),
            None,
        )
    if selected_lesion is None:
        selected_lesion = records[0] if records else None
    if selected_lesion:
        selected_lesion_id = str(selected_lesion.get("id_lesion") or "")

    catalogs = _catalog_context()
    form = _record_form(selected_lesion, args, catalogs)
    active_catalogs = {
        **catalogs,
        "zonas_cuerpo_options": _filter_zonas_segmento(catalogs, form["segmento_id"]),
        "zonas_especificas_options": _filter_zonas_anatomicas(catalogs, form["zona_cuerpo_id"]),
        "tipos_lesion_options": _filter_tipos_lesion(catalogs, form["mecanismo_id"]),
        "tipos_especificos_options": _filter_tipos_especificos(
            catalogs,
            form["mecanismo_id"],
            form["tipo_lesion_id"],
        ),
        "lateralidades": LATERALIDADES,
        "tipos_recidiva": TIPOS_RECIDIVA,
    }
    evolution_form = _evolution_form(args, selected_lesion)
    evolution_entries = _parse_json_list((selected_lesion or {}).get("evolucion"))

    preview_enabled = _request_bool(args.get("preview"))
    preview_payload: dict[str, Any] = {}
    summary = {
        "dias_baja_estimado": selected_lesion.get("dias_baja_estimado") if selected_lesion else 0,
        "estado_lesion": selected_lesion.get("estado_lesion") if selected_lesion else "-",
        "impacto_dias_baja_estimado": selected_lesion.get("impacto_dias_baja_estimado") if selected_lesion else "-",
        "dry_run": True,
    }
    if selected_lesion and preview_enabled:
        preview_payload, summary = _build_preview_payload(selected_lesion, form, evolution_form, catalogs)

    return {
        "competitions": competitions,
        "plantel": selected_plantel,
        "filters": {
            "posicion": selected_position or "",
            "jugadora": selected_player.get("id_jugadora") if selected_player else "",
            "tipo": selected_tipo,
            "status": selected_status,
            "posiciones": position_options,
            "jugadoras": players,
            "tipos": tipo_options,
            "status_options": STATUS_OPTIONS,
        },
        "player": selected_player,
        "records": records,
        "selected_lesion_id": selected_lesion_id,
        "selected_lesion": selected_lesion,
        "catalogs": active_catalogs,
        "form": form,
        "summary": summary,
        "evolution_entries": evolution_entries,
        "evolution_form": evolution_form,
        "preview_enabled": preview_enabled,
        "preview_payload": preview_payload,
        "save_errors": [],
    }
