"""
simulacion.py — Modelo probabilístico de un partido (solo stdlib).

A partir de las cuotas 1X2 deriva un marcador por **Poisson** y una crónica de
goles con sus minutos. El resultado (local/empate/visita) emerge del marcador,
así que SIEMPRE es consistente. No depende de BD ni de red: es testeable solo.

Idea:
  - prob. implícita de cada selección: p = 1/cuota, normalizadas (quita el margen).
  - goles esperados (λ) de cada equipo a partir de su fuerza relativa: el más
    favorito (mayor p) ataca más. El empate sale cuando los goles coinciden.
"""
import math
import random

# Goles esperados base de un equipo "promedio" y cuánto suma la ventaja.
_LAMBDA_BASE = 1.1
_LAMBDA_VENTAJA = 1.6
_MAX_GOLES = 7  # tope sano para que la mini-cancha no se desborde


def _poisson(lam: float) -> int:
    """Muestrea Poisson(lam) con el algoritmo de Knuth (sin numpy)."""
    objetivo = math.exp(-lam)
    k = 0
    producto = 1.0
    while True:
        producto *= random.random()
        if producto <= objetivo:
            return min(k, _MAX_GOLES)
        k += 1


def _lambdas(cuota_local: float, cuota_empate: float, cuota_visita: float):
    """Convierte cuotas en goles esperados (λ) de local y visita."""
    p_local = 1.0 / cuota_local
    p_empate = 1.0 / cuota_empate
    p_visita = 1.0 / cuota_visita
    total = p_local + p_empate + p_visita

    # Fuerza de cada equipo: su prob. de ganar + mitad de la prob. de empate,
    # normalizada entre ambos (suma 1) para repartir la "ventaja".
    fuerza_local = (p_local + p_empate / 2)
    fuerza_visita = (p_visita + p_empate / 2)
    suma = fuerza_local + fuerza_visita
    fl = fuerza_local / suma
    fv = fuerza_visita / suma

    lam_local = _LAMBDA_BASE + _LAMBDA_VENTAJA * fl
    lam_visita = _LAMBDA_BASE + _LAMBDA_VENTAJA * fv
    # `total` se usa solo para validar cuotas razonables (overround > 1).
    assert total > 0
    return lam_local, lam_visita


def simular_partido(cuota_local: float, cuota_empate: float, cuota_visita: float) -> dict:
    """
    Simula un partido y devuelve:
        {
          "marcador": {"local": int, "visita": int},
          "resultado": "local" | "empate" | "visita",
          "goles": [{"minuto": int, "equipo": "local"|"visita"}, ...]  # ordenado
        }
    """
    lam_local, lam_visita = _lambdas(cuota_local, cuota_empate, cuota_visita)
    goles_local = _poisson(lam_local)
    goles_visita = _poisson(lam_visita)

    if goles_local > goles_visita:
        resultado = "local"
    elif goles_visita > goles_local:
        resultado = "visita"
    else:
        resultado = "empate"

    # Minutos de gol: únicos cuando se puede, ordenados, etiquetados por equipo.
    total_goles = goles_local + goles_visita
    minutos = random.sample(range(1, 91), min(total_goles, 90))
    if total_goles > 90:  # caso extremo improbable
        minutos += [random.randint(1, 90) for _ in range(total_goles - 90)]
    equipos = ["local"] * goles_local + ["visita"] * goles_visita
    random.shuffle(equipos)
    goles = sorted(
        ({"minuto": m, "equipo": eq} for m, eq in zip(sorted(minutos), equipos)),
        key=lambda g: g["minuto"],
    )

    return {
        "marcador": {"local": goles_local, "visita": goles_visita},
        "resultado": resultado,
        "goles": goles,
    }
