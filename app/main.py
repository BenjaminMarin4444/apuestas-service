import json
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .auth import requiere_admin, usuario_actual
from .db import conexion, dict_cursor, esperar_bd, init_schema, sembrar_eventos
from .simulacion import simular_partido

SELECCIONES = {"local", "empate", "visita"}
CUOTA_COL = {"local": "cuota_local", "empate": "cuota_empate", "visita": "cuota_visita"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    esperar_bd()
    init_schema()
    yield


app = FastAPI(
    title="Apuestas Service",
    description="Apuestas deportivas del casino (Módulo 3 - ISY1101)",
    version="1.0.0",
    lifespan=lifespan,
)

_origenes = [o.strip() for o in os.getenv("CORS_ORIGIN", "http://localhost:4200").split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origenes,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ApuestaRequest(BaseModel):
    evento_id: int
    seleccion: str = Field(description="local | empate | visita")
    monto: float = Field(gt=0, description="Monto a apostar")


class ResolverRequest(BaseModel):
    resultado: str = Field(description="local | empate | visita")


@app.get("/livez", status_code=status.HTTP_200_OK)
def liveness_probe():
    return {"status": "alive"}


@app.get("/readyz")
def readiness_probe(response: Response):
    try:
        with conexion() as conn:
            with dict_cursor(conn) as cur:
                cur.execute("SELECT 1;")
                cur.fetchone()
        return {"status": "ready"}
    except Exception as e:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unready", "reason": str(e)}


@app.get("/api/apuestas/eventos")
def listar_eventos():
    with conexion() as conn:
        with dict_cursor(conn) as cur:
            cur.execute(
                """SELECT id, deporte, liga, equipo_local, equipo_visita,
                          badge_local, badge_visita, inicio,
                          cuota_local, cuota_empate, cuota_visita, estado
                     FROM eventos_deportivos
                    WHERE estado = 'abierto'
                    ORDER BY id"""
            )
            return {"eventos": cur.fetchall()}


@app.post("/api/apuestas", status_code=201)
def apostar(body: ApuestaRequest, usuario: dict = Depends(usuario_actual)):
    if body.seleccion not in SELECCIONES:
        raise HTTPException(status_code=400, detail="seleccion debe ser local | empate | visita")

    with conexion() as conn:
        with dict_cursor(conn) as cur:
            cur.execute(
                "SELECT * FROM eventos_deportivos WHERE id = %s",
                (body.evento_id,),
            )
            evento = cur.fetchone()
            if evento is None:
                raise HTTPException(status_code=404, detail="Evento no encontrado")
            if evento["estado"] != "abierto":
                raise HTTPException(status_code=409, detail="El evento no admite apuestas")

            cuota = float(evento[CUOTA_COL[body.seleccion]])
            ganancia = round(body.monto * cuota, 2)

            cur.execute(
                """UPDATE usuarios SET saldo = saldo - %s
                    WHERE id = %s AND saldo >= %s
                    RETURNING saldo""",
                (body.monto, usuario["id"], body.monto),
            )
            fila = cur.fetchone()
            if fila is None:
                raise HTTPException(status_code=409, detail="Saldo insuficiente")
            saldo = fila["saldo"]

            detalle = {
                "evento_id": evento["id"],
                "partido": f"{evento['equipo_local']} vs {evento['equipo_visita']}",
                "seleccion": body.seleccion,
                "cuota": cuota,
            }
            cur.execute(
                """INSERT INTO transacciones (usuario_id, tipo, monto, saldo_post, detalle)
                   VALUES (%s, 'apuesta', %s, %s, %s::jsonb)""",
                (usuario["id"], body.monto, saldo, json.dumps(detalle, ensure_ascii=False)),
            )
            cur.execute(
                """INSERT INTO apuestas
                     (usuario_id, evento_id, seleccion, monto, cuota, ganancia_potencial)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                (usuario["id"], evento["id"], body.seleccion, body.monto, cuota, ganancia),
            )
            apuesta_id = cur.fetchone()["id"]
        conn.commit()

    return {
        "apuesta_id": apuesta_id,
        "evento_id": evento["id"],
        "seleccion": body.seleccion,
        "monto": body.monto,
        "cuota": cuota,
        "ganancia_potencial": ganancia,
        "estado": "pendiente",
        "saldo": saldo,
    }


@app.get("/api/apuestas/mis-apuestas")
def mis_apuestas(usuario: dict = Depends(usuario_actual)):
    with conexion() as conn:
        with dict_cursor(conn) as cur:
            cur.execute(
                """SELECT a.id, a.seleccion, a.monto, a.cuota, a.ganancia_potencial,
                          a.estado, a.creada_en, a.resuelta_en,
                          e.deporte, e.liga, e.equipo_local, e.equipo_visita,
                          e.badge_local, e.badge_visita, e.resultado,
                          e.goles_local, e.goles_visita
                     FROM apuestas a
                     JOIN eventos_deportivos e ON e.id = a.evento_id
                    WHERE a.usuario_id = %s
                    ORDER BY a.creada_en DESC""",
                (usuario["id"],),
            )
            return {"apuestas": cur.fetchall()}


@app.post("/api/apuestas/eventos/{evento_id}/resolver")
def resolver_evento(evento_id: int, body: ResolverRequest, usuario: dict = Depends(requiere_admin)):
    if body.resultado not in SELECCIONES:
        raise HTTPException(status_code=400, detail="resultado debe ser local | empate | visita")

    pagadas = 0
    perdidas = 0
    with conexion() as conn:
        with dict_cursor(conn) as cur:
            cur.execute("SELECT * FROM eventos_deportivos WHERE id = %s", (evento_id,))
            evento = cur.fetchone()
            if evento is None:
                raise HTTPException(status_code=404, detail="Evento no encontrado")
            if evento["estado"] == "finalizado":
                raise HTTPException(status_code=409, detail="El evento ya fue resuelto")

            cur.execute(
                "UPDATE eventos_deportivos SET estado = 'finalizado', resultado = %s WHERE id = %s",
                (body.resultado, evento_id),
            )

            cur.execute(
                "SELECT * FROM apuestas WHERE evento_id = %s AND estado = 'pendiente'",
                (evento_id,),
            )
            for apuesta in cur.fetchall():
                if apuesta["seleccion"] == body.resultado:
                    cur.execute(
                        "UPDATE usuarios SET saldo = saldo + %s WHERE id = %s RETURNING saldo",
                        (apuesta["ganancia_potencial"], apuesta["usuario_id"]),
                    )
                    saldo = cur.fetchone()["saldo"]
                    detalle = {"apuesta_id": apuesta["id"], "evento_id": evento_id, "resultado": body.resultado}
                    cur.execute(
                        """INSERT INTO transacciones (usuario_id, tipo, monto, saldo_post, detalle)
                           VALUES (%s, 'premio', %s, %s, %s::jsonb)""",
                        (apuesta["usuario_id"], apuesta["ganancia_potencial"], saldo,
                         json.dumps(detalle, ensure_ascii=False)),
                    )
                    cur.execute(
                        "UPDATE apuestas SET estado = 'ganada', resuelta_en = NOW() WHERE id = %s",
                        (apuesta["id"],),
                    )
                    pagadas += 1
                else:
                    cur.execute(
                        "UPDATE apuestas SET estado = 'perdida', resuelta_en = NOW() WHERE id = %s",
                        (apuesta["id"],),
                    )
                    perdidas += 1
        conn.commit()

    return {
        "evento_id": evento_id,
        "resultado": body.resultado,
        "apuestas_ganadoras": pagadas,
        "apuestas_perdedoras": perdidas,
    }


def _liquidar_apuestas(cur, evento_id: int, resultado: str) -> tuple[int, int]:
    pagadas = 0
    perdidas = 0
    cur.execute(
        "SELECT * FROM apuestas WHERE evento_id = %s AND estado = 'pendiente'",
        (evento_id,),
    )
    for apuesta in cur.fetchall():
        if apuesta["seleccion"] == resultado:
            cur.execute(
                "UPDATE usuarios SET saldo = saldo + %s WHERE id = %s RETURNING saldo",
                (apuesta["ganancia_potencial"], apuesta["usuario_id"]),
            )
            saldo = cur.fetchone()["saldo"]
            detalle = {"apuesta_id": apuesta["id"], "evento_id": evento_id, "resultado": resultado}
            cur.execute(
                """INSERT INTO transacciones (usuario_id, tipo, monto, saldo_post, detalle)
                   VALUES (%s, 'premio', %s, %s, %s::jsonb)""",
                (apuesta["usuario_id"], apuesta["ganancia_potencial"], saldo,
                 json.dumps(detalle, ensure_ascii=False)),
            )
            cur.execute(
                "UPDATE apuestas SET estado = 'ganada', resuelta_en = NOW() WHERE id = %s",
                (apuesta["id"],),
            )
            pagadas += 1
        else:
            cur.execute(
                "UPDATE apuestas SET estado = 'perdida', resuelta_en = NOW() WHERE id = %s",
                (apuesta["id"],),
            )
            perdidas += 1
    return pagadas, perdidas


@app.post("/api/apuestas/eventos/{evento_id}/simular")
def simular_evento(evento_id: int, usuario: dict = Depends(usuario_actual)):
    with conexion() as conn:
        with dict_cursor(conn) as cur:
            cur.execute("SELECT * FROM eventos_deportivos WHERE id = %s", (evento_id,))
            evento = cur.fetchone()
            if evento is None:
                raise HTTPException(status_code=404, detail="Evento no encontrado")
            if evento["estado"] == "finalizado":
                raise HTTPException(status_code=409, detail="El evento ya fue simulado")

            sim = simular_partido(
                float(evento["cuota_local"]),
                float(evento["cuota_empate"]),
                float(evento["cuota_visita"]),
            )

            cur.execute(
                """UPDATE eventos_deportivos
                      SET estado = 'finalizado', resultado = %s,
                          goles_local = %s, goles_visita = %s, minutos_gol = %s::jsonb
                    WHERE id = %s""",
                (sim["resultado"], sim["marcador"]["local"], sim["marcador"]["visita"],
                 json.dumps(sim["goles"], ensure_ascii=False), evento_id),
            )
            pagadas, perdidas = _liquidar_apuestas(cur, evento_id, sim["resultado"])
        conn.commit()

    return {
        "evento_id": evento_id,
        "evento": {
            "equipo_local": evento["equipo_local"],
            "equipo_visita": evento["equipo_visita"],
            "badge_local": evento["badge_local"],
            "badge_visita": evento["badge_visita"],
            "liga": evento["liga"],
        },
        "marcador": sim["marcador"],
        "resultado": sim["resultado"],
        "goles": sim["goles"],
        "apuestas_ganadoras": pagadas,
        "apuestas_perdedoras": perdidas,
    }


@app.post("/api/apuestas/seed")
def resembrar(usuario: dict = Depends(requiere_admin)):
    return sembrar_eventos(forzar=True)


@app.post("/api/apuestas/reiniciar")
def reiniciar(usuario: dict = Depends(usuario_actual)):
    return sembrar_eventos(forzar=True)