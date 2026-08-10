from __future__ import annotations

import os
import re
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

if load_dotenv is not None:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

DEFAULT_ETL_SCHEMA = "etl"
DEFAULT_OBSERVER_SCHEMA = "observer"
DEFAULT_PUBLIC_SCHEMA = "public"
SCHEMA_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TABLE_COMPONENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TEMPLATE_REF_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _schema_name(env_name: str, default: str) -> str:
    value = os.getenv(env_name, default).strip()
    if not value:
        return default
    return value


def _quote_component(component: str) -> str:
    if not TABLE_COMPONENT_RE.match(component):
        raise ValueError(f"Invalid table identifier component: {component!r}")
    return f'"{component}"'


def _resolve_table_ref(raw_value: str) -> str:
    parts = [part.strip() for part in raw_value.split(".") if part.strip()]
    if not parts or len(parts) > 2:
        raise ValueError(f"Invalid table reference: {raw_value!r}")
    return ".".join(_quote_component(part) for part in parts)


def get_etl_schema() -> str:
    return _schema_name("ETL_SCHEMA", DEFAULT_ETL_SCHEMA)


def get_observer_schema() -> str:
    return _schema_name("OBSERVER_SCHEMA", DEFAULT_OBSERVER_SCHEMA)


def get_public_schema() -> str:
    return _schema_name("PUBLIC_SCHEMA", DEFAULT_PUBLIC_SCHEMA)


def get_table_ref(env_name: str) -> str:
    raw = os.getenv(env_name)
    value = (raw or "").strip()
    if not value:
        raise KeyError(f"Missing required table env var: {env_name}")
    return _resolve_table_ref(value)


def get_all_table_refs() -> dict[str, str]:
    refs: dict[str, str] = {}
    for key, value in os.environ.items():
        if key.endswith("_TABLE") and value.strip():
            refs[key] = _resolve_table_ref(value.strip())
    return refs


def render_sql_template(sql_text: str) -> str:
    refs = {match.group(1): get_table_ref(match.group(1)) for match in TEMPLATE_REF_RE.finditer(sql_text)}
    return TEMPLATE_REF_RE.sub(lambda match: refs[match.group(1)], sql_text)


def set_search_path(cursor, *schemas: str) -> None:
    names = [schema for schema in schemas if schema]
    if not names:
        return
    validated = []
    for schema in names:
        if not SCHEMA_NAME_RE.match(schema):
            raise ValueError(f"Invalid schema name: {schema!r}")
        validated.append(f'"{schema}"')
    cursor.execute(f"SET search_path TO {', '.join(validated)}")