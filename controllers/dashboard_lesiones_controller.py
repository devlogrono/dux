from __future__ import annotations

from flask import Blueprint, render_template, request
from flask_login import login_required

from dux.common.lesiones.services import (
    build_lesiones_grupal_context,
    build_lesiones_index_context,
    build_lesiones_individual_context,
)

bp = Blueprint("dashboard_lesiones", __name__, url_prefix="/dashboard/lesiones")


@bp.get("/")
@login_required
def index():
    context = build_lesiones_index_context(
        plantel=request.args.get("plantel") or None,
        period=request.args.get("period") or "semana",
        jugadora=request.args.get("jugadora") or None,
        tipo=request.args.get("tipo") or None,
        estado=request.args.get("estado") or None,
    )
    return render_template("dashboard/lesiones/index.html", **context)


@bp.get("/grupal")
@login_required
def grupal():
    context = build_lesiones_grupal_context(
        plantel=request.args.get("plantel") or None,
        posicion=request.args.get("posicion") or None,
        tipo=request.args.get("tipo") or None,
        period=request.args.get("period") or "mes",
        start_date=request.args.get("start_date") or None,
        end_date=request.args.get("end_date") or None,
        impact_tipo=request.args.get("impact_tipo") or None,
        impact_zona=request.args.get("impact_zona") or None,
        registro_estado=request.args.get("registro_estado") or None,
        registro_severidad=request.args.get("registro_severidad") or None,
        active_tab=request.args.get("active_tab") or "evolucion",
    )
    return render_template("dashboard/lesiones/grupal.html", **context)


@bp.get("/individual")
@login_required
def individual():
    context = build_lesiones_individual_context(
        plantel=request.args.get("plantel") or None,
        posicion=request.args.get("posicion") or None,
        jugadora=request.args.get("jugadora") or None,
        tipo=request.args.get("tipo") or None,
        impact_tipo=request.args.get("impact_tipo") or None,
        impact_zona=request.args.get("impact_zona") or None,
        active_tab=request.args.get("active_tab") or "historial",
    )
    return render_template("dashboard/lesiones/individual.html", **context)
