"""
db.py — Conexión a PostgreSQL (compartida con el casino-backend).

Patrón 12-factor: toda la configuración viene de variables de entorno.
Este servicio comparte la MISMA base de datos que casino-backend (lee/escribe
`usuarios` y `transacciones`). Sus tablas propias (`eventos_deportivos`,
`apuestas`) las crea al arrancar de forma idempotente.
"""
import os
import time

import psycopg2
import psycopg2.extras
import psycopg2.pool
from psycopg2 import extensions

# NUMERIC -> float (igual que casino-backend) para respuestas JSON nativas.
_DEC2FLOAT = extensions.new_type(
    extensions.DECIMAL.values,
    "DEC2FLOAT",
    lambda value, curs: float(value) if value is not None else None,
)
extensions.register_type(_DEC2FLOAT)

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "user": os.getenv("DB_USER", "casino"),
    "password": os.getenv("DB_PASSWORD", "casino"),
    "dbname": os.getenv("DB_NAME", "casino_db"),
}

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def esperar_bd(max_intentos: int = 30, espera_s: float = 2.0) -> None:
    """Reintenta hasta que Postgres acepte consultas (arranque asincrónico)."""
    global _pool
    ultimo_error = None
    for intento in range(1, max_intentos + 1):
        try:
            _pool = psycopg2.pool.ThreadedConnectionPool(1, 10, **DB_CONFIG)
            conn = _pool.getconn()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            _pool.putconn(conn)
            print(f"[PG] Conexión establecida (intento {intento})", flush=True)
            return
        except Exception as err:  # noqa: BLE001
            ultimo_error = err
            print(f"[PG] BD no disponible ({intento}/{max_intentos}): {err}", flush=True)
            time.sleep(espera_s)
    raise RuntimeError(f"No se pudo conectar a Postgres: {ultimo_error}")


class _Conexion:
    """Context manager: presta una conexión del pool y la devuelve siempre."""

    def __enter__(self):
        self.conn = _pool.getconn()
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.conn.rollback()
        _pool.putconn(self.conn)


def conexion() -> _Conexion:
    return _Conexion()


def dict_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


# ------------------------------------------------------------------
# Esquema propio (idempotente). usuarios / transacciones las crea
# casino-backend/db/init.sql en el initdb de Postgres.
# ------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS eventos_deportivos (
  id            SERIAL PRIMARY KEY,
  deporte       VARCHAR(40)  NOT NULL,
  equipo_local  VARCHAR(80)  NOT NULL,
  equipo_visita VARCHAR(80)  NOT NULL,
  inicio        TIMESTAMP,
  cuota_local   NUMERIC(6,2) NOT NULL,
  cuota_empate  NUMERIC(6,2) NOT NULL,
  cuota_visita  NUMERIC(6,2) NOT NULL,
  estado        VARCHAR(20)  NOT NULL DEFAULT 'abierto'
                CHECK (estado IN ('abierto','cerrado','finalizado')),
  resultado     VARCHAR(10)  CHECK (resultado IN ('local','empate','visita')),
  creado_en     TIMESTAMP    NOT NULL DEFAULT NOW()
);

-- Columnas del simulador / thesportsdb (idempotentes para BD ya existentes).
ALTER TABLE eventos_deportivos ADD COLUMN IF NOT EXISTS liga         VARCHAR(80);
ALTER TABLE eventos_deportivos ADD COLUMN IF NOT EXISTS badge_local  TEXT;
ALTER TABLE eventos_deportivos ADD COLUMN IF NOT EXISTS badge_visita TEXT;
ALTER TABLE eventos_deportivos ADD COLUMN IF NOT EXISTS goles_local  INTEGER;
ALTER TABLE eventos_deportivos ADD COLUMN IF NOT EXISTS goles_visita INTEGER;
ALTER TABLE eventos_deportivos ADD COLUMN IF NOT EXISTS minutos_gol  JSONB;

CREATE TABLE IF NOT EXISTS apuestas (
  id                  SERIAL PRIMARY KEY,
  usuario_id          INTEGER      NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
  evento_id           INTEGER      NOT NULL REFERENCES eventos_deportivos(id),
  seleccion           VARCHAR(10)  NOT NULL CHECK (seleccion IN ('local','empate','visita')),
  monto               NUMERIC(12,2) NOT NULL,
  cuota               NUMERIC(6,2)  NOT NULL,
  ganancia_potencial  NUMERIC(12,2) NOT NULL,
  estado              VARCHAR(10)  NOT NULL DEFAULT 'pendiente'
                      CHECK (estado IN ('pendiente','ganada','perdida')),
  creada_en           TIMESTAMP    NOT NULL DEFAULT NOW(),
  resuelta_en         TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_apuestas_usuario ON apuestas(usuario_id, creada_en DESC);
CREATE INDEX IF NOT EXISTS idx_apuestas_evento  ON apuestas(evento_id);
"""

# Índice único sobre el "partido": evita duplicar eventos en reinicios.
_INDICE_PARTIDO = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_evento_partido
  ON eventos_deportivos (deporte, equipo_local, equipo_visita);
"""

# Seed de respaldo (sin red): los 4 partidos clásicos del lab.
_SEED_FALLBACK = """
INSERT INTO eventos_deportivos
  (deporte, equipo_local, equipo_visita, cuota_local, cuota_empate, cuota_visita, liga) VALUES
  ('Fútbol', 'Colo-Colo',    'U. de Chile',  2.30, 3.10, 3.00, 'Liga de muestra'),
  ('Fútbol', 'Real Madrid',  'Barcelona',    2.50, 3.40, 2.60, 'Liga de muestra'),
  ('Fútbol', 'Boca Juniors', 'River Plate',  2.10, 3.20, 3.40, 'Liga de muestra'),
  ('Fútbol', 'Liverpool',    'Man. City',    2.75, 3.50, 2.40, 'Liga de muestra')
ON CONFLICT (deporte, equipo_local, equipo_visita) DO NOTHING;
"""

# Clásicos históricos con equipos mundialmente conocidos. Los escudos reales se
# traen de thesportsdb por nombre al sembrar. (deporte siempre 'Fútbol'.)
_CLASICOS = [
    ("Real Madrid", "Barcelona"),            # El Clásico
    ("Boca Juniors", "River Plate"),         # Superclásico
    ("Manchester United", "Manchester City"),# Derbi de Mánchester
    ("AC Milan", "Inter Milan"),             # Derby della Madonnina
    ("Liverpool", "Everton"),                # Derbi de Merseyside
    ("Arsenal", "Tottenham Hotspur"),        # North London Derby
    ("Bayern Munich", "Borussia Dortmund"),  # Der Klassiker
    ("Juventus", "Napoli"),
    ("Chelsea", "Arsenal"),
    ("Paris Saint-Germain", "Marseille"),    # Le Classique
]
# Cuántos clásicos sembrar.
_N_EVENTOS = int(os.getenv("APUESTAS_N_EVENTOS", str(len(_CLASICOS))))


def _cuotas_plausibles() -> tuple[float, float, float]:
    """1X2 con un favorito aleatorio y margen de casa (~1.08)."""
    import random

    fuerza_local = random.uniform(0.30, 0.70)
    fuerza_visita = 1.0 - fuerza_local
    cuota_empate_share = random.uniform(0.20, 0.30)  # el empate varía por partido
    p_local = fuerza_local * (1.0 - cuota_empate_share)
    p_visita = fuerza_visita * (1.0 - cuota_empate_share)
    p_empate = 1.0 - p_local - p_visita
    margen = 1.08
    return (
        round(margen / p_local, 2),
        round(margen / p_empate, 2),
        round(margen / p_visita, 2),
    )


def _sembrar_clasicos(cur) -> int:
    """Siembra clásicos históricos con escudos reales (thesportsdb). Devuelve cuántos insertó."""
    from .sportsdb import buscar_equipo

    insertados = 0
    for local_n, visita_n in _CLASICOS[:_N_EVENTOS]:
        el = buscar_equipo(local_n)
        ev = buscar_equipo(visita_n)
        if el is None and ev is None:
            continue  # sin red para este par; lo omite (el fallback cubre el caso global)
        nombre_l = el["nombre"] if el else local_n
        nombre_v = ev["nombre"] if ev else visita_n
        badge_l = el["badge"] if el else None
        badge_v = ev["badge"] if ev else None
        liga = (el and el["liga"]) or (ev and ev["liga"]) or "Clásicos"
        cl, ce, cv = _cuotas_plausibles()
        # Si el clásico ya existe (p. ej. finalizado de una corrida previa), se
        # REABRE como fixture fresco; si no, se inserta.
        cur.execute(
            """INSERT INTO eventos_deportivos
                 (deporte, equipo_local, equipo_visita, cuota_local, cuota_empate,
                  cuota_visita, liga, badge_local, badge_visita)
               VALUES ('Fútbol', %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (deporte, equipo_local, equipo_visita) DO UPDATE SET
                 estado = 'abierto', resultado = NULL, goles_local = NULL,
                 goles_visita = NULL, minutos_gol = NULL,
                 cuota_local = EXCLUDED.cuota_local, cuota_empate = EXCLUDED.cuota_empate,
                 cuota_visita = EXCLUDED.cuota_visita, liga = EXCLUDED.liga,
                 badge_local = EXCLUDED.badge_local, badge_visita = EXCLUDED.badge_visita""",
            (nombre_l, nombre_v, cl, ce, cv, liga, badge_l, badge_v),
        )
        insertados += cur.rowcount
    return insertados


def sembrar_eventos(forzar: bool = False) -> dict:
    """
    Siembra clásicos históricos (equipos famosos + escudos reales de thesportsdb).
    Si no se fuerza y ya hay eventos abiertos, no hace nada. Al forzar, primero
    limpia los eventos abiertos SIN apuestas (para refrescar la cartelera).
    Si la red falla, usa el seed de respaldo.
    """
    with conexion() as conn:
        with conn.cursor() as cur:
            if not forzar:
                cur.execute("SELECT COUNT(*) FROM eventos_deportivos WHERE estado = 'abierto'")
                if cur.fetchone()[0] > 0:
                    return {"sembrados": 0, "fuente": "ya_existian"}
            else:
                # limpia cartelera vieja abierta que nadie apostó
                cur.execute(
                    """DELETE FROM eventos_deportivos
                        WHERE estado = 'abierto'
                          AND id NOT IN (SELECT DISTINCT evento_id FROM apuestas)"""
                )

            insertados = _sembrar_clasicos(cur)
            fuente = "thesportsdb"
            if insertados == 0:
                cur.execute(_SEED_FALLBACK)
                insertados = cur.rowcount
                fuente = "fallback"
        conn.commit()
    print(f"[PG] Eventos sembrados: {insertados} (fuente: {fuente})", flush=True)
    return {"sembrados": insertados, "fuente": fuente}


def init_schema() -> None:
    with conexion() as conn:
        with conn.cursor() as cur:
            cur.execute(_SCHEMA)
            cur.execute(_INDICE_PARTIDO)
        conn.commit()
    sembrar_eventos(forzar=False)
    print("[PG] Esquema de apuestas verificado/sembrado", flush=True)


def ping() -> bool:
    try:
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001
        return False
