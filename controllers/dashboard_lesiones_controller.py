from __future__ import annotations

from flask import Blueprint, render_template, request
from flask_login import login_required

from dux.common.lesiones.services import build_lesiones_index_context

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
