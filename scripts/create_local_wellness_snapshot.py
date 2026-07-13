"""Create a small local Wellness snapshot for migration work.

The script reads from the database configured in SQLALCHEMY_DATABASE_URI and
writes to a local MySQL database. It intentionally does not store credentials.

Example:
    python scripts/create_local_wellness_snapshot.py \
        --target-uri mysql+pymysql://root:root@127.0.0.1:3306/db_dux_wellness_test \
        --days 90 \
        --plantel 1FF
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlparse, urlunparse

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


AUTH_TABLES = [
    "roles",
    "state_user",
    "permissions",
    "role_permissions",
    "users",
    "login_audit",
]
CATALOG_TABLES = [
    "plantel",
    "diccionario_competiciones",
    "competiciones",
    "tipo_carga",
    "estimulos_readaptacion",
    "tipo_condicion",
    "zonas_segmento",
    "zonas_anatomicas",
    "tipo_ausencia",
]
DATA_TABLES = [
    "futbolistas",
    "informacion_futbolistas",
    "wellness",
    "ausencias",
    "jornadas",
    "actas",
    "sustituciones",
]
ALL_TABLES = AUTH_TABLES + CATALOG_TABLES + DATA_TABLES


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create an anonymized local DB snapshot for Wellness migration."
    )
    parser.add_argument(
        "--source-uri",
        default=os.getenv("SQLALCHEMY_DATABASE_URI"),
        help="Remote/source SQLAlchemy URI. Defaults to SQLALCHEMY_DATABASE_URI.",
    )
    parser.add_argument(
        "--target-uri",
        default=os.getenv(
            "LOCAL_SNAPSHOT_DATABASE_URI",
            "mysql+pymysql://root:root@127.0.0.1:3306/db_dux_wellness_test",
        ),
        help="Local target SQLAlchemy URI.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Number of recent days of wellness data to copy.",
    )
    parser.add_argument(
        "--plantel",
        action="append",
        default=[],
        help="Plantel code to include. Can be passed more than once. Defaults to all.",
    )
    parser.add_argument(
        "--keep-observations",
        action="store_true",
        help="Keep free-text observations instead of replacing them with NULL.",
    )
    parser.add_argument(
        "--keep-player-names",
        action="store_true",
        help="Keep real player names so existing dashboards that join by name keep working.",
    )
    parser.add_argument(
        "--match-limit",
        type=int,
        default=300,
        help="Maximum number of recent match rows from jornadas to copy.",
    )
    return parser.parse_args()


def database_name(uri: str) -> str:
    parsed = urlparse(uri)
    db_name = parsed.path.lstrip("/")
    if not db_name:
        raise ValueError("Target URI must include a database name.")
    return db_name


def normalized_uri_without_credentials(uri: str) -> str:
    parsed = urlparse(uri)
    host = (parsed.hostname or "").lower()
    port = parsed.port or 3306
    return f"{parsed.scheme}://{host}:{port}/{parsed.path.lstrip('/')}"


def server_uri(uri: str) -> str:
    parsed = urlparse(uri)
    return urlunparse(parsed._replace(path="", params="", query="", fragment=""))


def quote_name(name: str) -> str:
    return f"`{name.replace('`', '``')}`"


def quote_default(value: Any) -> str:
    if value is None:
        return ""
    raw = str(value)
    if raw.upper() in {"CURRENT_TIMESTAMP", "CURRENT_TIMESTAMP()"}:
        return f" DEFAULT {raw}"
    if re.match(r"^[A-Za-z_]+\(.*\)$", raw):
        return f" DEFAULT {raw}"
    escaped = raw.replace("\\", "\\\\").replace("'", "''")
    return f" DEFAULT '{escaped}'"


def normalize_extra(value: Any) -> str:
    if not value:
        return ""
    parts = [part for part in str(value).split() if part.upper() != "DEFAULT_GENERATED"]
    return " ".join(parts)


def create_database(target_uri: str) -> None:
    db_name = database_name(target_uri)
    engine = create_engine(server_uri(target_uri), pool_pre_ping=True)
    with engine.begin() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {quote_name(db_name)}"))
        conn.execute(
            text(
                f"CREATE DATABASE {quote_name(db_name)} "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_spanish_ci"
            )
        )


def table_exists(engine: Engine, table_name: str) -> bool:
    with engine.connect() as conn:
        return bool(
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = DATABASE() AND table_name = :table_name"
                ),
                {"table_name": table_name},
            ).scalar()
        )


def create_table_without_foreign_keys(source: Engine, target: Engine, table_name: str) -> None:
    with source.connect() as conn:
        columns = conn.execute(text(f"SHOW FULL COLUMNS FROM {quote_name(table_name)}")).mappings().all()
        primary_rows = conn.execute(text(f"SHOW KEYS FROM {quote_name(table_name)} WHERE Key_name = 'PRIMARY'")).mappings().all()

    if not columns:
        raise RuntimeError(f"Could not inspect source table {table_name}.")

    primary_cols = [row["Column_name"] for row in sorted(primary_rows, key=lambda row: row["Seq_in_index"])]
    definitions = []

    for col in columns:
        line = f"{quote_name(col['Field'])} {col['Type']}"
        if col["Collation"]:
            line += f" COLLATE {col['Collation']}"
        line += " NULL" if col["Null"] == "YES" else " NOT NULL"
        line += quote_default(col["Default"])
        extra = normalize_extra(col["Extra"])
        if extra:
            line += f" {extra}"
        definitions.append(line)

    if primary_cols:
        definitions.append("PRIMARY KEY (" + ", ".join(quote_name(col) for col in primary_cols) + ")")

    ddl = (
        f"CREATE TABLE {quote_name(table_name)} (\n  "
        + ",\n  ".join(definitions)
        + "\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_spanish_ci"
    )

    with target.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {quote_name(table_name)}"))
        conn.execute(text(ddl))


def fetch_mappings(engine: Engine, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(text(sql), params or {}).mappings().all()]


def insert_rows(engine: Engine, table_name: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return

    columns = list(rows[0].keys())
    column_sql = ", ".join(quote_name(col) for col in columns)
    value_sql = ", ".join(f":{col}" for col in columns)

    with engine.begin() as conn:
        conn.execute(
            text(f"INSERT INTO {quote_name(table_name)} ({column_sql}) VALUES ({value_sql})"),
            rows,
        )


def anonymize_players(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for index, row in enumerate(rows, start=1):
        item = dict(row)
        item["nombre"] = f"Jugadora {index:03d}"
        item["apellido"] = "Anonima"
        if "fecha_nacimiento" in item:
            item["fecha_nacimiento"] = date(2000, 1, 1)
        out.append(item)
    return out


def anonymize_player_info(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        item = dict(row)
        item["foto_url"] = None
        item["foto_url_drive"] = None
        out.append(item)
    return out


def anonymize_text(rows: list[dict[str, Any]], keep_observations: bool) -> list[dict[str, Any]]:
    if keep_observations:
        return rows
    out = []
    for row in rows:
        item = dict(row)
        if "observacion" in item:
            item["observacion"] = None
        out.append(item)
    return out


def anonymize_users(rows: list[dict[str, Any]], admin_role_id: str | None) -> list[dict[str, Any]]:
    local_password_hash = hashlib.sha256("admin123".encode()).hexdigest()
    out = []
    for index, row in enumerate(rows, start=1):
        item = dict(row)
        item["email"] = "admin@example.com" if index == 1 else f"user{index:03d}@example.com"
        item["name"] = "Admin" if index == 1 else f"Usuario {index:03d}"
        item["lastname"] = "Local"
        item["password_hash"] = local_password_hash
        if index == 1 and admin_role_id and "role_id" in item:
            item["role_id"] = admin_role_id
        if "cellular" in item:
            item["cellular"] = None
        if "state_id" in item:
            item["state_id"] = 2
        out.append(item)
    return out


def build_filters(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    filters = ["w.fecha_sesion >= :start_date", "w.estatus_id <= 2"]
    params: dict[str, Any] = {"start_date": date.today() - timedelta(days=args.days)}

    if args.plantel:
        filters.append("f.competicion IN :planteles")
        params["planteles"] = tuple(args.plantel)

    return " AND ".join(filters), params


def admin_role_id(source: Engine) -> str | None:
    if not table_exists(source, "roles"):
        return None
    rows = fetch_mappings(
        source,
        "SELECT id FROM roles WHERE UPPER(name) = 'ADMIN' ORDER BY id LIMIT 1",
    )
    return rows[0]["id"] if rows else None


def selected_team_ids(source: Engine, planteles: list[str]) -> list[Any]:
    if not table_exists(source, "competiciones"):
        return []

    if planteles:
        rows = fetch_mappings(
            source,
            """
            SELECT DISTINCT id_equipo
            FROM competiciones
            WHERE competicion IN :planteles AND id_equipo IS NOT NULL
            """,
            {"planteles": tuple(planteles)},
        )
    else:
        rows = fetch_mappings(
            source,
            "SELECT DISTINCT id_equipo FROM competiciones WHERE id_equipo IS NOT NULL",
        )
    return [row["id_equipo"] for row in rows]


def copy_match_sample(
    source: Engine,
    target: Engine,
    args: argparse.Namespace,
    existing_player_ids: set[Any],
) -> None:
    if not all(table_exists(source, table) for table in ("jornadas", "actas", "sustituciones")):
        return

    team_ids = selected_team_ids(source, args.plantel)
    params: dict[str, Any] = {"limit": args.match_limit}
    where_parts = ["acta_id IS NOT NULL", "acta_id <> ''"]

    if team_ids:
        where_parts.append("(id_equipo_local IN :team_ids OR id_equipo_visitante IN :team_ids)")
        params["team_ids"] = tuple(team_ids)

    jornadas_rows = fetch_mappings(
        source,
        f"""
        SELECT *
        FROM jornadas
        WHERE {' AND '.join(where_parts)}
        ORDER BY fecha DESC
        LIMIT :limit
        """,
        params,
    )
    if not jornadas_rows and args.plantel:
        jornadas_rows = fetch_mappings(
            source,
            """
            SELECT *
            FROM jornadas
            WHERE acta_id IS NOT NULL AND acta_id <> ''
            ORDER BY fecha DESC
            LIMIT :limit
            """,
            {"limit": args.match_limit},
        )
    acta_ids = sorted({row["acta_id"] for row in jornadas_rows if row.get("acta_id")})

    actas_rows: list[dict[str, Any]] = []
    sustituciones_rows: list[dict[str, Any]] = []
    if acta_ids:
        actas_rows = fetch_mappings(
            source,
            "SELECT * FROM actas WHERE acta_id IN :acta_ids",
            {"acta_ids": tuple(acta_ids)},
        )
        sustituciones_rows = fetch_mappings(
            source,
            "SELECT * FROM sustituciones WHERE acta_id IN :acta_ids",
            {"acta_ids": tuple(acta_ids)},
        )

    player_names = sorted({row["jugador"] for row in actas_rows if row.get("jugador")})
    extra_players: list[dict[str, Any]] = []
    extra_info: list[dict[str, Any]] = []
    if player_names and table_exists(source, "futbolistas"):
        candidate_players = fetch_mappings(
            source,
            """
            SELECT *
            FROM futbolistas
            WHERE CONCAT(apellido, ', ', nombre) IN :player_names
            """,
            {"player_names": tuple(player_names)},
        )
        extra_players = [
            row for row in candidate_players if row.get("identificacion") not in existing_player_ids
        ]
        extra_ids = sorted({row["identificacion"] for row in extra_players if row.get("identificacion")})
        if extra_ids and table_exists(source, "informacion_futbolistas"):
            extra_info = fetch_mappings(
                source,
                "SELECT * FROM informacion_futbolistas WHERE identificacion IN :ids",
                {"ids": tuple(extra_ids)},
            )

    if extra_players:
        players_to_insert = extra_players if args.keep_player_names else anonymize_players(extra_players)
        insert_rows(target, "futbolistas", players_to_insert)
    if extra_info:
        insert_rows(target, "informacion_futbolistas", anonymize_player_info(extra_info))

    insert_rows(target, "jornadas", jornadas_rows)
    insert_rows(target, "actas", actas_rows)
    insert_rows(target, "sustituciones", sustituciones_rows)

    print(f"futbolistas_from_actas: {len(extra_players)} rows")
    print(f"informacion_futbolistas_from_actas: {len(extra_info)} rows")
    print(f"jornadas: {len(jornadas_rows)} rows")
    print(f"actas: {len(actas_rows)} rows")
    print(f"sustituciones: {len(sustituciones_rows)} rows")


def copy_snapshot(source: Engine, target: Engine, args: argparse.Namespace) -> None:
    required_source_tables = ["wellness", "futbolistas"]
    missing_required = [table for table in required_source_tables if not table_exists(source, table)]
    if missing_required:
        raise RuntimeError(
            "Source database is missing required tables: "
            + ", ".join(missing_required)
            + ". Check --source-uri or SQLALCHEMY_DATABASE_URI."
        )

    for table_name in ALL_TABLES:
        if not table_exists(source, table_name):
            print(f"skip missing table: {table_name}")
            continue
        create_table_without_foreign_keys(source, target, table_name)

    local_admin_role_id = admin_role_id(source)

    for table_name in AUTH_TABLES:
        if not table_exists(source, table_name):
            continue
        rows = fetch_mappings(source, f"SELECT * FROM {quote_name(table_name)}")
        if table_name == "users":
            rows = anonymize_users(rows, local_admin_role_id)
        elif table_name == "login_audit":
            rows = []
        insert_rows(target, table_name, rows)
        print(f"{table_name}: {len(rows)} rows")

    for table_name in CATALOG_TABLES:
        if not table_exists(source, table_name):
            continue
        rows = fetch_mappings(source, f"SELECT * FROM {quote_name(table_name)}")
        insert_rows(target, table_name, rows)
        print(f"{table_name}: {len(rows)} rows")

    for table_name in EMPTY_COMPAT_TABLES:
        if table_exists(source, table_name):
            print(f"{table_name}: 0 rows")

    where_sql, params = build_filters(args)
    wellness_rows = fetch_mappings(
        source,
        """
        SELECT w.*
        FROM wellness w
        LEFT JOIN futbolistas f ON w.id_jugadora = f.identificacion
        WHERE
        """
        + where_sql,
        params,
    )
    wellness_rows = anonymize_text(wellness_rows, args.keep_observations)
    player_ids = sorted({row["id_jugadora"] for row in wellness_rows if row.get("id_jugadora")})

    player_rows: list[dict[str, Any]] = []
    info_rows: list[dict[str, Any]] = []
    absence_rows: list[dict[str, Any]] = []

    if player_ids:
        player_rows = fetch_mappings(
            source,
            "SELECT * FROM futbolistas WHERE identificacion IN :ids",
            {"ids": tuple(player_ids)},
        )
        info_rows = fetch_mappings(
            source,
            "SELECT * FROM informacion_futbolistas WHERE identificacion IN :ids",
            {"ids": tuple(player_ids)},
        )
        absence_rows = fetch_mappings(
            source,
            "SELECT * FROM ausencias WHERE id_jugadora IN :ids",
            {"ids": tuple(player_ids)},
        )

    players_to_insert = player_rows if args.keep_player_names else anonymize_players(player_rows)
    insert_rows(target, "futbolistas", players_to_insert)
    insert_rows(target, "informacion_futbolistas", anonymize_player_info(info_rows))
    insert_rows(target, "wellness", wellness_rows)
    insert_rows(target, "ausencias", anonymize_text(absence_rows, args.keep_observations))

    print(f"futbolistas: {len(player_rows)} rows")
    print(f"informacion_futbolistas: {len(info_rows)} rows")
    print(f"wellness: {len(wellness_rows)} rows")
    print(f"ausencias: {len(absence_rows)} rows")

    copy_match_sample(source, target, args, set(player_ids))


def main() -> None:
    load_dotenv()
    args = parse_args()

    if not args.source_uri:
        raise SystemExit("Missing --source-uri or SQLALCHEMY_DATABASE_URI.")
    if normalized_uri_without_credentials(args.source_uri) == normalized_uri_without_credentials(args.target_uri):
        raise SystemExit(
            "Source and target databases are the same. "
            "Point SQLALCHEMY_DATABASE_URI/--source-uri to the remote read database "
            "and --target-uri to the local snapshot database."
        )

    print(f"Creating local snapshot database: {database_name(args.target_uri)}")
    print(f"Days: {args.days}")
    print(f"Planteles: {', '.join(args.plantel) if args.plantel else 'all'}")
    print(f"Match limit: {args.match_limit}")

    create_database(args.target_uri)
    source = create_engine(args.source_uri, pool_pre_ping=True)
    target = create_engine(args.target_uri, pool_pre_ping=True)
    copy_snapshot(source, target, args)
    print("Done.")


if __name__ == "__main__":
    main()
