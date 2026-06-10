from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any

from flask_login import current_user
from sqlalchemy import text

from dux import db
from dux.common.lesiones.queries import (
    get_latest_lesion_id_for_player,
    get_lesiones_catalog,
    get_lesiones_competitions,
    get_lesiones_players,
)
from dux.common.lesiones.transforms import POSITION_OPTIONS, coerce_date, normalize_position


DEFAULT_PLANTEL = "1FF"
LATERALIDADES = ["NO APLICA", "DERECHA", "IZQUIERDA", "BILATERAL"]
TIPOS_RECIDIVA = ["TEMPRANA (<= 2 MESES)", "TARDIA (2-12 MESES)"]
GRAVEDAD_DIAS = [
    {"nombre": "LEVE", "dias_min": 1, "dias_max": 3},
    {"nombre": "MODERADA", "dias_min": 4, "dias_max": 7},
    {"nombre": "GRAVE", "dias_min": 8, "dias_max": 28},
    {"nombre": "MUY GRAVE", "dias_min": 29, "dias_max": None},
]
LESION_INSERT_COLUMNS = [
    "id_lesion",
    "id_jugadora",
    "posicion",
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
    "evolucion",
    "fecha_hora_registro",
    "usuario",
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
        item["initial"] = str((item.get("nombre_jugadora") or "?")[:1]).upper()
        item["has_photo"] = bool(
            item["id_jugadora"]
            and (str(item.get("foto_url") or "").strip() or str(item.get("foto_url_drive") or "").strip())
        )
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
    catalogs = {
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
    return catalogs


def _find_by_id(rows: list[dict[str, Any]], value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    for row in rows:
        if str(row.get("id")) == str(value):
            return row
    return None


def _filter_zonas_segmento(catalogs: dict[str, Any], segmento_id: str | None) -> list[dict[str, Any]]:
    if not segmento_id:
        return []
    return [
        row for row in catalogs["zonas_segmento"]
        if str(row.get("segmento_id")) == str(segmento_id)
    ]


def _filter_zonas_anatomicas(catalogs: dict[str, Any], zona_cuerpo_id: str | None) -> list[dict[str, Any]]:
    if not zona_cuerpo_id:
        return []
    return [
        row for row in catalogs["zonas_anatomicas"]
        if str(row.get("zona_id")) == str(zona_cuerpo_id)
    ]


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


def _request_bool(value: str | None) -> bool:
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


def _severity_for_days(days: int | None) -> str | None:
    if days is None or days <= 0:
        return None
    for item in GRAVEDAD_DIAS:
        min_days = item["dias_min"]
        max_days = item["dias_max"]
        if days >= min_days and (max_days is None or days <= max_days):
            return item["nombre"]
    return None


def _selected_treatments(raw_values: list[str] | None, valid_rows: list[dict[str, Any]]) -> list[str]:
    valid_by_id = {str(row.get("id")): str(row.get("nombre") or "").upper() for row in valid_rows}
    return [valid_by_id[value] for value in (raw_values or []) if value in valid_by_id]


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


def generate_lesion_id(
    nombre: str,
    id_jugadora: str,
    ultima_lesion_id: str | None = None,
    fecha: str | None = None,
) -> str:
    parts = str(nombre or id_jugadora or "").strip().split()
    initials = "".join(part[0].upper() for part in parts if part) or str(id_jugadora or "L")[:2].upper()
    date_part = fecha or datetime.now().strftime("%Y%m%d")

    number = 1
    if ultima_lesion_id:
        match = re.search(r"-(\d+)$", str(ultima_lesion_id))
        if match:
            number = int(match.group(1)) + 1

    return f"{initials}{date_part}-{number}"


def validate_lesion_registration_record(
    args: dict[str, Any],
    selected_player: dict[str, Any] | None,
    catalogs: dict[str, Any],
) -> list[str]:
    errors: list[str] = []

    if not selected_player:
        errors.append("Selecciona una jugadora valida")

    fecha_lesion = coerce_date(args.get("fecha_lesion"))
    fecha_alta = coerce_date(args.get("fecha_alta_diagnostico"))
    implica_baja = _request_bool(args.get("implica_baja"))

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

    return errors


def build_lesion_registration_record(
    args: dict[str, Any],
    selected_player: dict[str, Any],
    catalogs: dict[str, Any],
) -> dict[str, Any]:
    fecha_lesion = coerce_date(args.get("fecha_lesion"))
    fecha_alta = coerce_date(args.get("fecha_alta_diagnostico"))
    implica_baja = _request_bool(args.get("implica_baja"))
    es_recidiva = _request_bool(args.get("es_recidiva"))

    days = 0
    if implica_baja and fecha_lesion and fecha_alta:
        days = max(0, (fecha_alta - fecha_lesion).days)

    treatments = _selected_treatments(_arg_list(args, "tipo_tratamiento"), catalogs["tratamientos"])
    estado = "ACTIVO" if implica_baja else "OBSERVACION"
    fecha_hora_registro = datetime.now()
    latest_id = get_latest_lesion_id_for_player(str(selected_player["id_jugadora"]))
    id_lesion = generate_lesion_id(
        selected_player.get("nombre_jugadora") or "",
        str(selected_player["id_jugadora"]),
        latest_id,
        fecha_hora_registro.strftime("%Y%m%d"),
    )

    return {
        "id_lesion": id_lesion,
        "id_jugadora": str(selected_player["id_jugadora"]),
        "posicion": str(selected_player.get("posicion") or "").upper() or None,
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
        "fecha_observacion_activa": None,
        "fecha_observacion_inactiva": None,
        "fecha_alta_medica": None,
        "fecha_alta_deportiva": None,
        "estado_lesion": estado,
        "diagnostico": str(args.get("diagnostico") or "").strip(),
        "descripcion": str(args.get("descripcion") or "").strip(),
        "evolucion": json.dumps([], ensure_ascii=False),
        "fecha_hora_registro": fecha_hora_registro,
        "usuario": _username(),
    }


def save_lesion_registration_record(record: dict[str, Any]) -> str:
    params = {column: record.get(column) for column in LESION_INSERT_COLUMNS}
    columns_sql = ", ".join(LESION_INSERT_COLUMNS)
    values_sql = ", ".join(f":{column}" for column in LESION_INSERT_COLUMNS)
    sql = text(f"INSERT INTO lesiones ({columns_sql}) VALUES ({values_sql});")

    try:
        db.session.execute(sql, params)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return str(record["id_lesion"])


def save_lesion_registration(args: dict[str, Any]) -> tuple[bool, str | None, list[str]]:
    selected_plantel = args.get("plantel") or DEFAULT_PLANTEL
    players = _normalize_players(get_lesiones_players(selected_plantel))
    selected_position = normalize_position(args.get("posicion")) or None
    if selected_position:
        players = [player for player in players if player.get("posicion") == selected_position]

    selected_player = _select_player(players, args.get("jugadora"))
    catalogs = _catalog_context()
    errors = validate_lesion_registration_record(args, selected_player, catalogs)
    if errors:
        return False, None, errors

    record = build_lesion_registration_record(args, selected_player, catalogs)
    id_lesion = save_lesion_registration_record(record)
    return True, id_lesion, []


def _build_preview_payload(
    args: dict[str, Any],
    selected_player: dict[str, Any] | None,
    catalogs: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    fecha_lesion = coerce_date(args.get("fecha_lesion")) or date.today()
    implica_baja = _request_bool(args.get("implica_baja"))
    es_recidiva = _request_bool(args.get("es_recidiva"))
    fecha_alta = coerce_date(args.get("fecha_alta_diagnostico"))

    errors = []
    required = {
        "lugar_id": "Lugar",
        "segmento_id": "Region anatomica",
        "zona_cuerpo_id": "Zona anatomica",
        "tipo_lesion_id": "Tipo de lesion",
        "mecanismo_id": "Mecanismo de lesion",
    }
    for key, label in required.items():
        if not args.get(key):
            errors.append(f"Falta: {label}")
    if not str(args.get("personal_reporta") or "").strip():
        errors.append("Falta: Personal medico que reporta")

    days = 0
    date_error = False
    if implica_baja:
        if not fecha_alta:
            errors.append("Si implica baja, informa alta deportiva estimada")
        elif fecha_alta < fecha_lesion:
            date_error = True
            errors.append("La fecha de alta estimada no puede ser anterior a la fecha de lesion")
        else:
            days = max(0, (fecha_alta - fecha_lesion).days)

    estado = "ACTIVO" if implica_baja and not date_error and fecha_alta else "OBSERVACION"
    gravedad = _severity_for_days(days)
    treatments = _selected_treatments(_arg_list(args, "tipo_tratamiento"), catalogs["tratamientos"])

    tipo_recidiva = args.get("tipo_recidiva") if es_recidiva else "NO APLICA"
    username = str(getattr(current_user, "email", "") or getattr(current_user, "username", "") or "")
    payload = {
        "id_jugadora": selected_player.get("id_jugadora") if selected_player else None,
        "posicion": selected_player.get("posicion") if selected_player else None,
        "fecha_lesion": fecha_lesion.isoformat(),
        "lugar_id": args.get("lugar_id") or None,
        "segmento_id": args.get("segmento_id") or None,
        "zona_cuerpo_id": args.get("zona_cuerpo_id") or None,
        "zona_especifica_id": args.get("zona_especifica_id") or None,
        "lateralidad": args.get("lateralidad") or None,
        "tipo_lesion_id": args.get("tipo_lesion_id") or None,
        "tipo_especifico_id": args.get("tipo_especifico_id") or None,
        "es_recidiva": es_recidiva,
        "tipo_recidiva": tipo_recidiva,
        "dias_baja_estimado": days,
        "impacto_dias_baja_estimado": gravedad,
        "mecanismo_id": args.get("mecanismo_id") or None,
        "tipo_tratamiento": treatments,
        "personal_reporta": str(args.get("personal_reporta") or "").strip(),
        "fecha_alta_diagnostico": fecha_alta.isoformat() if fecha_alta and implica_baja else None,
        "estado_lesion": estado,
        "diagnostico": str(args.get("diagnostico") or "").strip(),
        "descripcion": str(args.get("descripcion") or "").strip(),
        "evolucion": [],
        "usuario": username,
    }
    summary = {
        "dias_baja_estimado": days,
        "estado_lesion": estado,
        "impacto_dias_baja_estimado": gravedad or "-",
        "dry_run": True,
    }
    return payload, summary, errors


def build_lesiones_registro_context(args: dict[str, Any]) -> dict[str, Any]:
    selected_plantel = args.get("plantel") or DEFAULT_PLANTEL
    competitions = get_lesiones_competitions()
    players = _normalize_players(get_lesiones_players(selected_plantel))
    position_options = _position_options(players)
    selected_position = normalize_position(args.get("posicion")) or None
    if selected_position and selected_position not in position_options:
        selected_position = None
    if selected_position:
        players = [player for player in players if player.get("posicion") == selected_position]

    selected_player = _select_player(players, args.get("jugadora"))
    catalogs = _catalog_context()

    today = date.today()
    form = {
        "fecha_lesion": (coerce_date(args.get("fecha_lesion")) or today).isoformat(),
        "segmento_id": args.get("segmento_id") or "",
        "zona_cuerpo_id": args.get("zona_cuerpo_id") or "",
        "zona_especifica_id": args.get("zona_especifica_id") or "",
        "lugar_id": args.get("lugar_id") or "",
        "mecanismo_id": args.get("mecanismo_id") or "",
        "tipo_lesion_id": args.get("tipo_lesion_id") or "",
        "tipo_especifico_id": args.get("tipo_especifico_id") or "",
        "lateralidad": args.get("lateralidad") or "NO APLICA",
        "diagnostico": args.get("diagnostico") or "",
        "es_recidiva": _request_bool(args.get("es_recidiva")),
        "tipo_recidiva": args.get("tipo_recidiva") or "",
        "implica_baja": _request_bool(args.get("implica_baja")),
        "fecha_alta_diagnostico": (
            coerce_date(args.get("fecha_alta_diagnostico")) or (today + timedelta(days=1))
        ).isoformat(),
        "tipo_tratamiento": _arg_list(args, "tipo_tratamiento"),
        "personal_reporta": args.get("personal_reporta") or "",
        "descripcion": args.get("descripcion") or "",
    }

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
    if form["zona_cuerpo_id"] and form["zona_cuerpo_id"] not in {str(row.get("id")) for row in active_catalogs["zonas_cuerpo_options"]}:
        form["zona_cuerpo_id"] = ""
        form["zona_especifica_id"] = ""
    if form["zona_especifica_id"] and form["zona_especifica_id"] not in {str(row.get("id")) for row in active_catalogs["zonas_especificas_options"]}:
        form["zona_especifica_id"] = ""
    if form["tipo_lesion_id"] and form["tipo_lesion_id"] not in {str(row.get("id")) for row in active_catalogs["tipos_lesion_options"]}:
        form["tipo_lesion_id"] = ""
        form["tipo_especifico_id"] = ""
        active_catalogs["tipos_especificos_options"] = []
    if form["tipo_especifico_id"] and form["tipo_especifico_id"] not in {str(row.get("id")) for row in active_catalogs["tipos_especificos_options"]}:
        form["tipo_especifico_id"] = ""

    payload, summary, validation_errors = _build_preview_payload(form, selected_player, catalogs)

    return {
        "competitions": competitions,
        "plantel": selected_plantel,
        "filters": {
            "posicion": selected_position or "",
            "jugadora": selected_player.get("id_jugadora") if selected_player else "",
            "posiciones": position_options,
            "jugadoras": players,
        },
        "player": selected_player,
        "catalogs": active_catalogs,
        "form": form,
        "summary": summary,
        "validation_errors": validation_errors,
        "preview_payload": payload,
    }
