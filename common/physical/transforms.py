from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _f0(value: Any) -> float:
    return _to_float(value) or 0.0


def normalize_isak_numeric(record: dict[str, Any]) -> dict[str, Any]:
    normalized = {}

    for key, value in record.items():
        if isinstance(value, Decimal):
            normalized[key] = float(value)
        else:
            normalized[key] = value

    return normalized


def normalize_isak_record(record: dict[str, Any]) -> dict[str, Any]:
    normalized = {}

    for field, value in record.items():
        if field.startswith("_"):
            continue

        if value == "None":
            normalized[field] = None
            continue

        if isinstance(value, dt.datetime):
            normalized[field] = value.replace(microsecond=0)
            continue

        normalized[field] = value

    return normalized


def _required_numbers(raw: dict[str, Any], fields: list[str]) -> dict[str, float] | None:
    values = {}
    for field in fields:
        value = _to_float(raw.get(field))
        if value is None:
            return None
        values[field] = value
    return values


def calcular_masa_osea_excel(raw: dict[str, Any]) -> tuple[float, float] | None:
    values = _required_numbers(
        raw,
        [
            "talla_corporal_cm",
            "perimetro_cabeza",
            "biacromial",
            "bi_iliocrestideo",
            "humeral_biepicondilar",
            "femoral_biepicondilar",
        ],
    )
    if not values or values["talla_corporal_cm"] <= 0:
        return None

    factor_talla = 170.18 / values["talla_corporal_cm"]
    z_cabeza = (values["perimetro_cabeza"] - 56.0) / 1.44
    masa_osea_cabeza = (z_cabeza * 0.18) + 1.2

    suma_diametros = (
        values["biacromial"]
        + values["bi_iliocrestideo"]
        + (values["humeral_biepicondilar"] * 2.0)
        + (values["femoral_biepicondilar"] * 2.0)
    )

    z_cuerpo = ((suma_diametros * factor_talla) - 98.88) / 5.33
    masa_osea_cuerpo = ((z_cuerpo * 1.34) + 6.7) / (factor_talla**3)
    masa_osea_total = masa_osea_cabeza + masa_osea_cuerpo

    return round(masa_osea_total, 4), round(z_cuerpo, 4)


def calcular_masa_piel_excel(raw: dict[str, Any]) -> float | None:
    values = _required_numbers(raw, ["peso_bruto_kg", "talla_corporal_cm"])
    if not values or values["peso_bruto_kg"] <= 0 or values["talla_corporal_cm"] <= 0:
        return None

    grosor_piel = 1.96
    constante = 73.074

    sexo_id = raw.get("sexo_id")
    sexo = str(raw.get("sexo", "")).strip().upper()
    if sexo_id == 1 or sexo == "M":
        grosor_piel = 2.07
        constante = 68.308

    edad = _to_float(raw.get("edad"))
    if edad is not None and edad < 12:
        constante = 70.691

    area_superficial = (
        constante
        * (values["peso_bruto_kg"] ** 0.425)
        * (values["talla_corporal_cm"] ** 0.725)
    ) / 10000.0
    return round(area_superficial * grosor_piel * 1.05, 4)


def calcular_masa_residual_excel(raw: dict[str, Any]) -> tuple[float, float] | None:
    values = _required_numbers(
        raw,
        [
            "talla_sentado_cm",
            "perimetro_cintura_minima",
            "pliegue_abdominal",
            "torax_transverso",
            "torax_antero_posterior",
        ],
    )
    if not values or values["talla_sentado_cm"] <= 0:
        return None

    per_cintura_corregido = values["perimetro_cintura_minima"] - (
        values["pliegue_abdominal"] * 0.3141
    )
    suma_torax = (
        values["torax_transverso"]
        + values["torax_antero_posterior"]
        + per_cintura_corregido
    )

    factor = 89.92 / values["talla_sentado_cm"]
    z_residual = ((suma_torax * factor) - 109.35) / 7.08
    masa_residual = ((z_residual * 1.24) + 6.1) / (factor**3)

    return round(masa_residual, 4), round(z_residual, 4)


def calcular_masa_muscular_excel(raw: dict[str, Any]) -> tuple[float, float] | None:
    values = _required_numbers(
        raw,
        [
            "talla_corporal_cm",
            "perimetro_brazo_relajado",
            "perimetro_antebrazo_maximo",
            "perimetro_muslo_maximo",
            "perimetro_pantorrilla_maxima",
            "perimetro_torax_mesoesternal",
            "pliegue_triceps",
            "pliegue_muslo_frontal",
            "pliegue_pantorrilla_maxima",
            "pliegue_subescapular",
        ],
    )
    if not values or values["talla_corporal_cm"] <= 0:
        return None

    pi = 3.141
    brazo_corr = values["perimetro_brazo_relajado"] - ((values["pliegue_triceps"] * pi) / 10.0)
    muslo_corr = values["perimetro_muslo_maximo"] - ((values["pliegue_muslo_frontal"] * pi) / 10.0)
    pantorrilla_corr = values["perimetro_pantorrilla_maxima"] - (
        (values["pliegue_pantorrilla_maxima"] * pi) / 10.0
    )
    torax_corr = values["perimetro_torax_mesoesternal"] - (
        (values["pliegue_subescapular"] * pi) / 10.0
    )

    suma_muscular = (
        brazo_corr
        + values["perimetro_antebrazo_maximo"]
        + muslo_corr
        + pantorrilla_corr
        + torax_corr
    )

    factor_talla = 170.18 / values["talla_corporal_cm"]
    z_muscular = ((suma_muscular * factor_talla) - 207.21) / 13.74
    masa_muscular = ((z_muscular * 5.4) + 24.5) / (factor_talla**3)

    return round(masa_muscular, 4), round(z_muscular, 4)


def calcular_masa_adiposa_excel(
    suma_6: float,
    talla_corporal_cm: float,
    peso_kg: float,
) -> tuple[float, float] | None:
    if talla_corporal_cm <= 0 or peso_kg <= 0:
        return None

    factor_talla = 170.18 / talla_corporal_cm
    z_pliegues = (suma_6 * factor_talla - 116.41) / 34.79
    masa_adiposa_rel = ((z_pliegues * 5.85) + 25.6) / (factor_talla**3)

    return round(masa_adiposa_rel, 4), round(z_pliegues, 4)


def indice_masa(masa: float | None, talla_m: float | None) -> float | None:
    if masa is None or talla_m is None or talla_m <= 0:
        return None
    return round(masa / (talla_m**2), 4)


def indice_musculo_oseo(masa_muscular: float | None, masa_osea: float | None) -> float | None:
    if masa_muscular is None or masa_osea is None or masa_osea <= 0:
        return None
    return round(masa_muscular / masa_osea, 4)


def indice_musculo_lastre(
    musculo: float | None,
    adiposa: float | None,
    residual: float | None,
) -> float | None:
    if musculo is None or adiposa is None or residual is None:
        return None
    lastre = adiposa + residual
    if lastre <= 0:
        return None
    return round(musculo / lastre, 4)


def calcular_indice_lastre(
    masa_muscular_kg: float | None,
    suma_5_masas_kg: float | None,
    talla_corporal_cm: float | None,
) -> float | None:
    if masa_muscular_kg is None or suma_5_masas_kg is None or not talla_corporal_cm:
        return None
    if talla_corporal_cm <= 0:
        return None

    lastre = suma_5_masas_kg - masa_muscular_kg
    return round((lastre * 1000) / (talla_corporal_cm**2), 4)


def ajustar_masa_por_porcentaje(
    *,
    masa_kg: float,
    peso_estructurado_kg: float,
    diferencia_peso_kg: float,
) -> dict[str, float | None]:
    if peso_estructurado_kg <= 0:
        return {
            "pct": None,
            "ajuste_kg": 0.0,
            "masa_ajustada_kg": masa_kg,
        }

    pct_actual = masa_kg / peso_estructurado_kg
    ajuste_kg = diferencia_peso_kg * pct_actual
    masa_ajustada = masa_kg - ajuste_kg

    return {
        "pct": round(pct_actual * 100, 2),
        "ajuste_kg": round(ajuste_kg, 3),
        "masa_ajustada_kg": round(masa_ajustada, 3),
    }


def sumar_ajustes_masas(*ajustes_masas: dict[str, Any]) -> dict[str, float]:
    suma_pct = 0.0
    suma_ajuste = 0.0
    suma_kg = 0.0

    for ajuste in ajustes_masas:
        if not isinstance(ajuste, dict):
            continue
        suma_pct += _f0(ajuste.get("pct"))
        suma_ajuste += _f0(ajuste.get("ajuste_kg"))
        suma_kg += _f0(ajuste.get("masa_ajustada_kg"))

    return {
        "pct": round(suma_pct),
        "ajuste_kg": round(suma_ajuste, 2),
        "masa_ajustada_kg": round(suma_kg, 2),
    }


def ajustar_masas_por_masa_osea_ref(
    *,
    peso_kg: float,
    aj_adiposa: dict[str, Any],
    aj_muscular: dict[str, Any],
    aj_osea: dict[str, Any],
    aj_residual: dict[str, Any],
    aj_piel: dict[str, Any],
    masa_osea_ref_kg: float | None,
) -> dict[str, float]:
    masa_osea_actual = _f0(aj_osea.get("masa_ajustada_kg"))
    mor = _f0(masa_osea_ref_kg) if masa_osea_ref_kg is not None else masa_osea_actual
    mor = max(0.0, mor)

    delta_osea = mor - masa_osea_actual
    suma_4_cruda = (
        _f0(aj_adiposa.get("masa_ajustada_kg"))
        + _f0(aj_muscular.get("masa_ajustada_kg"))
        + _f0(aj_residual.get("masa_ajustada_kg"))
        + _f0(aj_piel.get("masa_ajustada_kg"))
    )

    if suma_4_cruda <= 0:
        return {
            "masa_adiposa_kg": 0.0,
            "masa_muscular_kg": 0.0,
            "masa_residual_kg": 0.0,
            "masa_piel_kg": 0.0,
            "masa_osea_kg": round(mor, 3),
            "peso_estructurado_kg": round(_f0(peso_kg), 3),
            "suma_4_masas_kg": 0.0,
            "suma_5_masas_kg": round(mor, 3),
            "delta_osea_kg": round(delta_osea, 3),
        }

    pct4_adiposa = _f0(aj_adiposa.get("masa_ajustada_kg")) / suma_4_cruda
    pct4_muscular = _f0(aj_muscular.get("masa_ajustada_kg")) / suma_4_cruda
    pct4_residual = _f0(aj_residual.get("masa_ajustada_kg")) / suma_4_cruda
    pct4_piel = _f0(aj_piel.get("masa_ajustada_kg")) / suma_4_cruda

    adiposa_corr = max(0.0, _f0(aj_adiposa.get("masa_ajustada_kg")) - delta_osea * pct4_adiposa)
    muscular_corr = max(0.0, _f0(aj_muscular.get("masa_ajustada_kg")) - delta_osea * pct4_muscular)
    residual_corr = max(0.0, _f0(aj_residual.get("masa_ajustada_kg")) - delta_osea * pct4_residual)
    piel_corr = max(0.0, _f0(aj_piel.get("masa_ajustada_kg")) - delta_osea * pct4_piel)

    suma_4_corr = adiposa_corr + muscular_corr + residual_corr + piel_corr
    suma_5_corr = suma_4_corr + mor

    return {
        "masa_adiposa_kg": round(adiposa_corr, 3),
        "masa_muscular_kg": round(muscular_corr, 3),
        "masa_residual_kg": round(residual_corr, 3),
        "masa_piel_kg": round(piel_corr, 3),
        "masa_osea_kg": round(mor, 3),
        "suma_4_masas_kg": round(suma_4_corr, 3),
        "suma_5_masas_kg": round(suma_5_corr, 3),
        "delta_osea_kg": round(delta_osea, 3),
        "peso_estructurado_kg": round(_f0(peso_kg), 3),
    }


def ajuste_alometrico(valor: float, talla_cm: float, tipo: int) -> float | None:
    if talla_cm <= 0:
        return None
    if tipo == 1:
        return round(valor * (170.18 / talla_cm) ** 3, 2)
    return round(valor * (170.18 / talla_cm), 2)


def _suma_6_pliegues(raw: dict[str, Any]) -> float | None:
    values = _required_numbers(
        raw,
        [
            "pliegue_triceps",
            "pliegue_subescapular",
            "pliegue_supraespinal",
            "pliegue_abdominal",
            "pliegue_muslo_frontal",
            "pliegue_pantorrilla_maxima",
        ],
    )
    if not values:
        return None
    return round(sum(values.values()), 2)


def calcular_antropometria(raw: dict[str, Any]) -> dict[str, Any]:
    peso = _to_float(raw.get("peso_bruto_kg"))
    talla_corporal_cm = _to_float(raw.get("talla_corporal_cm"))

    if not peso or peso <= 0 or not talla_corporal_cm or talla_corporal_cm <= 0:
        return {}

    talla_m = talla_corporal_cm / 100
    suma_6 = _suma_6_pliegues(raw)
    if suma_6 is None:
        return {}

    adiposa = calcular_masa_adiposa_excel(
        suma_6=suma_6,
        talla_corporal_cm=talla_corporal_cm,
        peso_kg=peso,
    )
    osea = calcular_masa_osea_excel(raw)
    residual = calcular_masa_residual_excel(raw)
    masa_piel = calcular_masa_piel_excel(raw)
    muscular = calcular_masa_muscular_excel(raw)

    if not adiposa or not osea or not residual or masa_piel is None or not muscular:
        return {"suma_6_pliegues_mm": suma_6}

    masa_adiposa, z_adiposa = adiposa
    masa_osea, z_osea = osea
    masa_residual, z_residual = residual
    masa_muscular, z_muscular = muscular

    peso_estructurado = masa_adiposa + masa_muscular + masa_osea + masa_residual + masa_piel
    diferencia_peso = peso_estructurado - peso
    diferencia_pct = ((peso_estructurado - peso) / peso) * 100 if peso > 0 else 0.0

    ajuste_adiposa = ajustar_masa_por_porcentaje(
        masa_kg=masa_adiposa,
        peso_estructurado_kg=peso_estructurado,
        diferencia_peso_kg=diferencia_peso,
    )
    ajuste_muscular = ajustar_masa_por_porcentaje(
        masa_kg=masa_muscular,
        peso_estructurado_kg=peso_estructurado,
        diferencia_peso_kg=diferencia_peso,
    )
    ajuste_osea = ajustar_masa_por_porcentaje(
        masa_kg=masa_osea,
        peso_estructurado_kg=peso_estructurado,
        diferencia_peso_kg=diferencia_peso,
    )
    ajuste_residual = ajustar_masa_por_porcentaje(
        masa_kg=masa_residual,
        peso_estructurado_kg=peso_estructurado,
        diferencia_peso_kg=diferencia_peso,
    )
    ajuste_piel = ajustar_masa_por_porcentaje(
        masa_kg=masa_piel,
        peso_estructurado_kg=peso_estructurado,
        diferencia_peso_kg=diferencia_peso,
    )

    ajuste_peso_estructurado = sumar_ajustes_masas(
        ajuste_adiposa,
        ajuste_muscular,
        ajuste_residual,
        ajuste_piel,
        ajuste_osea,
    )
    ajuste_peso_estructurado["ajuste_alometrico"] = ajuste_alometrico(
        valor=ajuste_peso_estructurado["masa_ajustada_kg"],
        talla_cm=talla_corporal_cm,
        tipo=1,
    )

    ajuste = ajustar_masas_por_masa_osea_ref(
        peso_kg=peso,
        aj_adiposa=ajuste_adiposa,
        aj_muscular=ajuste_muscular,
        aj_osea=ajuste_osea,
        aj_residual=ajuste_residual,
        aj_piel=ajuste_piel,
        masa_osea_ref_kg=_to_float(ajuste_osea.get("masa_ajustada_kg")),
    )

    idx_musculo_oseo = indice_musculo_oseo(
        ajuste["masa_muscular_kg"],
        ajuste["masa_osea_kg"],
    )

    return {
        "metodo": "ISAK",
        "metodo_masa_osea": "ROCHA",
        "suma_6_pliegues_mm": suma_6,
        "masa_adiposa_kg": round(masa_adiposa, 2),
        "z_adiposa": round(z_adiposa, 2),
        "ajuste_adiposa": ajuste_adiposa,
        "masa_muscular_kg": round(masa_muscular, 2),
        "z_muscular": round(z_muscular, 2),
        "ajuste_muscular": ajuste_muscular,
        "masa_osea_kg": round(masa_osea, 2),
        "z_osea": round(z_osea, 2),
        "ajuste_osea": ajuste_osea,
        "masa_residual_kg": round(masa_residual, 2),
        "z_residual": round(z_residual, 2),
        "ajuste_residual": ajuste_residual,
        "masa_piel_kg": round(masa_piel, 2),
        "ajuste_piel": ajuste_piel,
        "idx_adiposo": indice_masa(ajuste["masa_adiposa_kg"], talla_m),
        "idx_muscular": indice_masa(ajuste["masa_muscular_kg"], talla_m),
        "idx_oseo": indice_masa(ajuste["masa_osea_kg"], talla_m),
        "idx_residual": indice_masa(ajuste["masa_residual_kg"], talla_m),
        "idx_piel": indice_masa(ajuste["masa_piel_kg"], talla_m),
        "idx_musculo_oseo": idx_musculo_oseo,
        "idx_musculo_lastre": indice_musculo_lastre(
            ajuste["masa_muscular_kg"],
            ajuste["masa_adiposa_kg"],
            ajuste["masa_residual_kg"],
        ),
        "idx_lastre": calcular_indice_lastre(
            ajuste["masa_muscular_kg"],
            ajuste["suma_5_masas_kg"],
            talla_corporal_cm,
        ),
        "peso_estructurado_kg": round(peso_estructurado, 3),
        "diferencia_peso": round(diferencia_peso, 3),
        "diferencia_peso_pct": round(diferencia_pct, 2),
        "ajuste_peso_estructurado": ajuste_peso_estructurado,
        "ajuste_adiposa_pct": ajuste_adiposa.get("pct"),
        "ajuste_muscular_pct": ajuste_muscular.get("pct"),
    }


def build_record_antropometrico(raw_record: dict[str, Any]) -> dict[str, Any]:
    record = normalize_isak_record(raw_record)
    record_numeric = normalize_isak_numeric(record)
    calculos = calcular_antropometria(record_numeric)

    return {
        **record_numeric,
        **calculos,
    }
