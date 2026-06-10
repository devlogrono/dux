from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from dux.common.lesiones.followup import build_lesiones_seguimiento_context, save_lesion_followup
from dux.common.lesiones.registration import build_lesiones_registro_context, save_lesion_registration
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


@bp.get("/registro")
@login_required
def registro():
    context = build_lesiones_registro_context(request.args)
    return render_template("dashboard/lesiones/registro.html", **context)


@bp.get("/seguimiento")
@login_required
def seguimiento():
    context = build_lesiones_seguimiento_context(request.args)
    return render_template("dashboard/lesiones/seguimiento.html", **context)


@bp.post("/seguimiento")
@login_required
def seguimiento_save():
    action = request.form.get("action")
    if action != "save_followup":
        return redirect(url_for("dashboard_lesiones.seguimiento"))

    try:
        success, id_lesion, errors = save_lesion_followup(request.form)
    except Exception as exc:
        context = build_lesiones_seguimiento_context(request.form)
        context["save_errors"] = [f"No se pudo actualizar el seguimiento: {exc}"]
        return render_template("dashboard/lesiones/seguimiento.html", **context), 500

    if not success:
        context = build_lesiones_seguimiento_context(request.form)
        context["save_errors"] = errors
        return render_template("dashboard/lesiones/seguimiento.html", **context), 400

    flash("Seguimiento actualizado correctamente.", "success")
    return redirect(
        url_for(
            "dashboard_lesiones.seguimiento",
            plantel=request.form.get("plantel") or None,
            posicion=request.form.get("posicion") or None,
            jugadora=request.form.get("jugadora") or None,
            tipo=request.form.get("tipo") or None,
            status=request.form.get("status") or "todas",
            lesion=id_lesion,
        )
        + "#seguimiento-editor"
    )


@bp.post("/registro")
@login_required
def registro_save():
    action = request.form.get("action")
    if action != "save_registration":
        return redirect(url_for("dashboard_lesiones.registro"))

    try:
        success, id_lesion, errors = save_lesion_registration(request.form)
    except Exception as exc:
        context = build_lesiones_registro_context(request.form)
        context["save_errors"] = [f"No se pudo guardar la lesion: {exc}"]
        return render_template("dashboard/lesiones/registro.html", **context), 500

    if not success:
        context = build_lesiones_registro_context(request.form)
        context["save_errors"] = errors
        context["validation_errors"] = errors
        return render_template("dashboard/lesiones/registro.html", **context), 400

    flash(f"Lesion {id_lesion} guardada correctamente.", "success")
    return redirect(
        url_for(
            "dashboard_lesiones.individual",
            plantel=request.form.get("plantel") or None,
            jugadora=request.form.get("jugadora") or None,
            active_tab="registros",
        )
    )
