from datetime import date, timedelta

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError

from dux import cache, db

bp = Blueprint("dashboard_wellness", __name__, url_prefix="/dashboard/wellness")

WELLNESS_COLUMNS = ["recuperacion", "energia", "sueno", "stress", "dolor"]
PERIOD_OPTIONS = [
    {"id": "7", "label": "Últimos 7 días", "days": 7},
    {"id": "30", "label": "Últimos 30 días", "days": 30},
    {"id": "90", "label": "Últimos 90 días", "days": 90},
    {"id": "365", "label": "Últimos 365 días", "days": 365},
]
DEFAULT_PERIOD = "30"
DEFAULT_PLANTEL = "1FF"


def _user_cache_key():
    user_id = current_user.id if current_user.is_authenticated else "anon"
    return f"view//{request.path}?{request.query_string.decode()}&_uid={user_id}"


def _as_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_metric(value):
    return round(value, 1) if value is not None else None


def _record_score(row):
    values = [_as_float(row.get(column)) for column in WELLNESS_COLUMNS]
    values = [v for v in values if v is not None]
    if not values:
        return None
    return sum(values)


def _avg(values):
    values = [v for v in values if v is not None]
    if not values:
        return None
    return sum(values) / len(values)


def _clamp_days(days):
    return min(max(days, 1), 365)


def _resolve_period_selection(period, days=None):
    period_ids = {option["id"] for option in PERIOD_OPTIONS}
    if period in period_ids:
        selected_period = period
    elif days:
        try:
            selected_period = str(_clamp_days(int(days)))
        except (TypeError, ValueError):
            selected_period = DEFAULT_PERIOD
        if selected_period not in period_ids:
            selected_period = DEFAULT_PERIOD
    else:
        selected_period = DEFAULT_PERIOD

    selected_days = next(
        option["days"] for option in PERIOD_OPTIONS if option["id"] == selected_period
    )
    return selected_period, selected_days


def _valid_values(selected, available):
    available_set = set(available)
    return [value for value in selected if value in available_set]


def _selected_planteles(selected, available):
    valid_selected = _valid_values(selected, available)
    if valid_selected:
        return valid_selected
    if DEFAULT_PLANTEL in available:
        return [DEFAULT_PLANTEL]
    return []


def _filter_jugadoras_by_plantel(jugadoras, planteles):
    if not planteles:
        return jugadoras
    planteles_set = set(planteles)
    return [jugadora for jugadora in jugadoras if jugadora["plantel"] in planteles_set]


def _fetch_filter_options():
    sql = """
        SELECT DISTINCT
            f.identificacion AS id_jugadora,
            f.nombre,
            f.apellido,
            f.competicion AS plantel,
            w.tipo
        FROM wellness w
        LEFT JOIN futbolistas f ON w.id_jugadora = f.identificacion
        WHERE (w.estatus_id IS NULL OR w.estatus_id <= 2)
        ORDER BY f.competicion ASC, f.apellido ASC, f.nombre ASC
    """
    rows = db.session.execute(text(sql)).mappings().all()

    planteles = []
    seen_planteles = set()
    jugadoras = []
    seen_jugadoras = set()
    tipos = []
    seen_tipos = set()
    for row in rows:
        plantel = row["plantel"]
        if plantel and plantel not in seen_planteles:
            planteles.append(plantel)
            seen_planteles.add(plantel)

        tipo = row["tipo"]
        if tipo and tipo not in seen_tipos:
            tipos.append(tipo)
            seen_tipos.add(tipo)

        jugadora_id = row["id_jugadora"]
        if not jugadora_id or jugadora_id in seen_jugadoras:
            continue
        nombre_completo = f"{row['apellido'] or ''}, {row['nombre'] or ''}".strip(", ")
        jugadoras.append(
            {
                "id": jugadora_id,
                "nombre": nombre_completo or jugadora_id,
                "plantel": plantel,
            }
        )
        seen_jugadoras.add(jugadora_id)

    return planteles, jugadoras, tipos


def _fetch_player_options():
    sql = """
        SELECT DISTINCT
            identificacion AS id_jugadora,
            nombre,
            apellido,
            competicion AS plantel
        FROM futbolistas
        WHERE identificacion IS NOT NULL
          AND (genero = 'F' OR genero IS NULL)
          AND (id_estado = 1 OR id_estado IS NULL)
        ORDER BY competicion ASC, apellido ASC, nombre ASC
    """
    rows = db.session.execute(text(sql)).mappings().all()

    planteles = []
    seen_planteles = set()
    jugadoras = []
    for row in rows:
        plantel = row["plantel"]
        if plantel and plantel not in seen_planteles:
            planteles.append(plantel)
            seen_planteles.add(plantel)

        nombre_completo = f"{row['apellido'] or ''}, {row['nombre'] or ''}".strip(", ")
        jugadoras.append(
            {
                "id": row["id_jugadora"],
                "nombre": nombre_completo or row["id_jugadora"],
                "plantel": plantel,
            }
        )

    return planteles, jugadoras


def _fetch_pain_zone_options():
    try:
        rows = db.session.execute(text(
            "SELECT id, nombre FROM zonas_segmento ORDER BY nombre ASC"
        )).mappings().all()
    except SQLAlchemyError:
        return []

    return [{"id": row["id"], "nombre": row["nombre"]} for row in rows]


def _fetch_data_entry_options(selected_planteles=None):
    planteles, jugadoras = _fetch_player_options()
    selected_planteles = _selected_planteles(selected_planteles or [], planteles)
    visible_jugadoras = _filter_jugadoras_by_plantel(jugadoras, selected_planteles)
    return {
        "planteles": planteles,
        "jugadoras": visible_jugadoras,
        "pain_zones": _fetch_pain_zone_options(),
        "selected_planteles": selected_planteles,
        "turnos": ["Turno 1", "Turno 2", "Turno 3"],
        "wellness_fields": [
            "Recuperación",
            "Energía",
            "Sueño",
            "Estrés",
            "Dolor",
        ],
    }


def _parse_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _validate_checkin_form(form, available_jugadoras):
    errors = []
    available_ids = {jugadora["id"] for jugadora in available_jugadoras}

    id_jugadora = form.get("id_jugadora")
    if id_jugadora not in available_ids:
        errors.append("Selecciona una jugadora válida.")

    fecha_sesion = form.get("fecha_sesion") or date.today().isoformat()
    try:
        date.fromisoformat(fecha_sesion)
    except ValueError:
        errors.append("La fecha de sesión no es válida.")

    turno = form.get("turno") or "Turno 1"
    if turno not in ["Turno 1", "Turno 2", "Turno 3"]:
        errors.append("Selecciona un turno válido.")

    record = {
        "id_jugadora": id_jugadora,
        "fecha_sesion": fecha_sesion,
        "tipo": "checkin",
        "turno": turno,
        "periodizacion_tactica": form.get("periodizacion_tactica") or "",
        "recuperacion": _parse_int(form.get("recuperacion")),
        "fatiga": _parse_int(form.get("fatiga")),
        "sueno": _parse_int(form.get("sueno")),
        "stress": _parse_int(form.get("stress")),
        "dolor": _parse_int(form.get("dolor")),
        "id_zona_segmento_dolor": _parse_int(form.get("id_zona_segmento_dolor")),
        "observacion": form.get("observacion") or "",
        "usuario": (
            getattr(current_user, "name", None)
            or getattr(current_user, "email", None)
            or "unknown"
        ),
    }

    for field in ["recuperacion", "fatiga", "sueno", "stress", "dolor"]:
        value = record[field]
        if value is None:
            errors.append(f"Completa el campo {field}.")
        elif not 1 <= value <= 5:
            errors.append(f"El campo {field} debe estar entre 1 y 5.")

    if record["dolor"] is not None and record["dolor"] > 1 and record["id_zona_segmento_dolor"] is None:
        errors.append("Selecciona una zona de dolor.")

    return record, errors


def _checkin_exists(record):
    sql = """
        SELECT id
        FROM wellness
        WHERE id_jugadora = :id_jugadora
          AND fecha_sesion = :fecha_sesion
          AND turno = :turno
          AND (estatus_id IS NULL OR estatus_id <= 2)
        LIMIT 1
    """
    row = db.session.execute(
        text(sql),
        {
            "id_jugadora": record["id_jugadora"],
            "fecha_sesion": record["fecha_sesion"],
            "turno": record["turno"],
        },
    ).first()
    return row is not None


def _insert_checkin(record):
    sql = """
        INSERT INTO wellness (
            id_jugadora, fecha_sesion, tipo, turno, periodizacion_tactica,
            recuperacion, fatiga, sueno, stress, dolor, id_zona_segmento_dolor,
            observacion, usuario, estatus_id
        ) VALUES (
            :id_jugadora, :fecha_sesion, :tipo, :turno, :periodizacion_tactica,
            :recuperacion, :fatiga, :sueno, :stress, :dolor, :id_zona_segmento_dolor,
            :observacion, :usuario, 1
        )
    """
    db.session.execute(text(sql), record)
    db.session.commit()
    cache.clear()


def _fetch_wellness_records(planteles, jugadoras, tipos, since, limit=None):
    sql = """
        SELECT
            w.id,
            w.id_jugadora,
            f.nombre,
            f.apellido,
            f.competicion AS plantel,
            w.fecha_sesion,
            w.tipo,
            w.turno,
            w.recuperacion,
            w.fatiga AS energia,
            w.sueno,
            w.stress,
            w.dolor,
            w.minutos_sesion,
            w.rpe,
            w.ua,
            w.observacion,
            w.fecha_hora_registro,
            w.usuario
        FROM wellness w
        LEFT JOIN futbolistas f ON w.id_jugadora = f.identificacion
        WHERE (w.estatus_id IS NULL OR w.estatus_id <= 2)
          AND w.fecha_sesion >= :since
    """
    params = {"since": since}
    bindparams = []

    if planteles:
        sql += " AND f.competicion IN :planteles"
        params["planteles"] = planteles
        bindparams.append(bindparam("planteles", expanding=True))
    if jugadoras:
        sql += " AND w.id_jugadora IN :jugadoras"
        params["jugadoras"] = jugadoras
        bindparams.append(bindparam("jugadoras", expanding=True))
    if tipos:
        sql += " AND w.tipo IN :tipos"
        params["tipos"] = tipos
        bindparams.append(bindparam("tipos", expanding=True))

    sql += " ORDER BY w.fecha_sesion DESC, w.fecha_hora_registro DESC"
    if limit:
        sql += " LIMIT :limit"
        params["limit"] = limit

    statement = text(sql)
    if bindparams:
        statement = statement.bindparams(*bindparams)

    rows = db.session.execute(statement, params).mappings().all()
    records = []
    for row in rows:
        record = dict(row)
        record["nombre_jugadora"] = (
            f"{record.get('apellido') or ''}, {record.get('nombre') or ''}".strip(", ")
            or record.get("id_jugadora")
        )
        record["wellness_score"] = _round_metric(_record_score(record))
        record["rpe"] = _round_metric(_as_float(record.get("rpe")))
        record["ua"] = _round_metric(_as_float(record.get("ua")))
        records.append(record)

    return records


def _is_checkin(record):
    return str(record.get("tipo") or "").lower().replace("-", "") == "checkin"


def _build_alerts(records):
    checkin_records = [record for record in records if _is_checkin(record)]
    base_records = checkin_records or records

    players = {}
    for record in base_records:
        player_key = record.get("id_jugadora") or record.get("nombre_jugadora")
        if not player_key:
            continue
        players.setdefault(player_key, {"nombre": record.get("nombre_jugadora"), "values": []})
        players[player_key]["values"].append(record)

    risky_players = []
    for player in players.values():
        column_means = []
        for column in WELLNESS_COLUMNS:
            column_mean = _avg([_as_float(record.get(column)) for record in player["values"]])
            if column_mean is not None:
                column_means.append(column_mean)

        wellness_mean_1_5 = _avg(column_means)
        pain_mean = _avg([_as_float(record.get("dolor")) for record in player["values"]])
        is_risky = (
            (wellness_mean_1_5 is not None and wellness_mean_1_5 > 3)
            or (pain_mean is not None and pain_mean > 3)
        )
        if is_risky:
            risky_players.append(
                {
                    "nombre": player["nombre"],
                    "prom_w_1_5": _round_metric(wellness_mean_1_5),
                    "dolor_mean": _round_metric(pain_mean),
                }
            )

    total_players = len(players)
    alerts_count = len(risky_players)
    alerts_pct = round((alerts_count / total_players) * 100, 1) if total_players else 0

    return {
        "count": alerts_count,
        "total_jugadoras": total_players,
        "pct": alerts_pct,
        "jugadoras": risky_players,
    }


def _wellness_status(value):
    if value is None:
        return "sin datos"
    if value > 20:
        return "óptimo"
    if value >= 15:
        return "moderado"
    return "en fatiga"


def _rpe_status(value):
    if value in (None, 0):
        return "sin datos"
    if value < 5:
        return "bajo"
    if value <= 7:
        return "moderado"
    return "alto"


def _build_summary(records):
    alerts = _build_alerts(records)
    wellness_average = _round_metric(_avg([r["wellness_score"] for r in records]))
    rpe_average = _round_metric(_avg([r["rpe"] for r in records]))
    return {
        "total": len(records),
        "wellness_promedio": wellness_average,
        "wellness_estado": _wellness_status(wellness_average),
        "rpe_promedio": rpe_average,
        "rpe_estado": _rpe_status(rpe_average),
        "ua_total": _round_metric(sum([r["ua"] or 0 for r in records])),
        "alertas_count": alerts["count"],
        "alertas_total_jugadoras": alerts["total_jugadoras"],
        "alertas_pct": alerts["pct"],
        "alertas_jugadoras": alerts["jugadoras"],
    }


def _date_key(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _build_daily_charts(records):
    daily = {}
    for record in records:
        date_value = record.get("fecha_sesion")
        if not date_value:
            continue
        key = _date_key(date_value)
        daily.setdefault(key, {"wellness": [], "rpe": [], "ua": 0})
        daily[key]["wellness"].append(record.get("wellness_score"))
        daily[key]["rpe"].append(record.get("rpe"))
        daily[key]["ua"] += record.get("ua") or 0

    labels = sorted(daily.keys())
    return {
        "labels": labels,
        "wellness": [_round_metric(_avg(daily[label]["wellness"])) for label in labels],
        "rpe": [_round_metric(_avg(daily[label]["rpe"])) for label in labels],
        "ua": [_round_metric(daily[label]["ua"]) for label in labels],
    }


@bp.get("/")
@login_required
@cache.cached(timeout=3600, make_cache_key=_user_cache_key)
def index():
    requested_planteles = request.args.getlist("plantel")
    requested_jugadoras = request.args.getlist("jugadora")
    requested_tipos = request.args.getlist("tipo")
    selected_period, days = _resolve_period_selection(
        request.args.get("period"),
        request.args.get("days"),
    )
    since = date.today() - timedelta(days=days)

    error = None
    planteles = []
    jugadoras = []
    visible_jugadoras = []
    tipos = []
    selected_planteles = []
    selected_jugadoras = []
    selected_tipos = []
    records = []
    summary = _build_summary(records)
    charts = _build_daily_charts(records)

    try:
        planteles, jugadoras, tipos = _fetch_filter_options()
        selected_planteles = _selected_planteles(requested_planteles, planteles)
        visible_jugadoras = _filter_jugadoras_by_plantel(jugadoras, selected_planteles)
        selected_jugadoras = _valid_values(
            requested_jugadoras,
            [jugadora["id"] for jugadora in visible_jugadoras],
        )
        selected_tipos = _valid_values(requested_tipos, tipos)
        records = _fetch_wellness_records(
            selected_planteles,
            selected_jugadoras,
            selected_tipos,
            since,
        )
        summary = _build_summary(records)
        charts = _build_daily_charts(records)
    except SQLAlchemyError:
        db.session.rollback()
        error = "No se pudieron cargar los registros de Wellness."

    return render_template(
        "dashboard/wellness.html",
        error=error,
        planteles=planteles,
        jugadoras=jugadoras,
        visible_jugadoras=visible_jugadoras,
        tipos=tipos,
        selected_planteles=selected_planteles,
        selected_jugadoras=selected_jugadoras,
        selected_tipos=selected_tipos,
        period_options=PERIOD_OPTIONS,
        selected_period=selected_period,
        days=days,
        records=records,
        display_records=records[:200],
        summary=summary,
        charts=charts,
    )


@bp.route("/registro/", methods=["GET", "POST"])
@login_required
def registro():
    options = {
        "planteles": [],
        "jugadoras": [],
        "pain_zones": [],
        "selected_planteles": [],
        "turnos": ["Turno 1", "Turno 2", "Turno 3"],
        "wellness_fields": [
            "Recuperación",
            "Energía",
            "Sueño",
            "Estrés",
            "Dolor",
        ],
    }
    error = None
    form_errors = []
    form_data = {
        "fecha_sesion": date.today().isoformat(),
        "turno": "Turno 1",
    }
    saved = request.args.get("saved") == "1"

    try:
        requested_planteles = request.form.getlist("plantel") or request.args.getlist("plantel")
        options = _fetch_data_entry_options(requested_planteles)
        form_data.update(request.form.to_dict())

        if request.method == "POST":
            record, form_errors = _validate_checkin_form(request.form, options["jugadoras"])
            form_data.update(record)

            if not form_errors and _checkin_exists(record):
                form_errors.append("Ya existe un Check-in para esta jugadora, fecha y turno.")

            if not form_errors:
                _insert_checkin(record)
                return redirect(url_for("dashboard_wellness.registro", saved=1))
    except SQLAlchemyError:
        db.session.rollback()
        error = "No se pudo procesar el registro Wellness."

    return render_template(
        "dashboard/wellness_registro.html",
        error=error,
        form_errors=form_errors,
        form_data=form_data,
        saved=saved,
        **options,
    )
