"""
Tests del modelo de simulación (app/simulacion.py).

Lógica pura (solo stdlib) → se puede correr local sin BD ni red:
    python3 -m pytest apuestas-service/tests/test_simulacion.py
o sin pytest:
    python3 apuestas-service/tests/test_simulacion.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.simulacion import simular_partido  # noqa: E402


def test_estructura_resultado():
    r = simular_partido(2.0, 3.0, 4.0)
    assert set(r.keys()) == {"marcador", "resultado", "goles"}
    assert set(r["marcador"].keys()) == {"local", "visita"}
    assert r["resultado"] in {"local", "empate", "visita"}
    assert isinstance(r["goles"], list)


def test_marcador_consistente_con_resultado():
    """El resultado SIEMPRE corresponde al marcador (por construcción)."""
    for _ in range(500):
        r = simular_partido(1.8, 3.5, 4.5)
        gl, gv = r["marcador"]["local"], r["marcador"]["visita"]
        esperado = "local" if gl > gv else "visita" if gv > gl else "empate"
        assert r["resultado"] == esperado, (gl, gv, r["resultado"])


def test_goles_coinciden_con_marcador():
    """La cantidad de goles por equipo coincide con el marcador."""
    for _ in range(200):
        r = simular_partido(2.5, 3.2, 2.7)
        locales = sum(1 for g in r["goles"] if g["equipo"] == "local")
        visitas = sum(1 for g in r["goles"] if g["equipo"] == "visita")
        assert locales == r["marcador"]["local"]
        assert visitas == r["marcador"]["visita"]


def test_minutos_validos_y_ordenados():
    for _ in range(200):
        r = simular_partido(2.0, 3.0, 3.5)
        minutos = [g["minuto"] for g in r["goles"]]
        assert minutos == sorted(minutos)
        for m in minutos:
            assert 1 <= m <= 90
        for g in r["goles"]:
            assert g["equipo"] in {"local", "visita"}


def test_favorito_gana_mas_seguido():
    """Con cuota local muy baja (favorito claro), el local debe ganar la mayoría."""
    n = 2000
    gana_local = 0
    for _ in range(n):
        r = simular_partido(1.30, 5.0, 9.0)  # local ampliamente favorito
        if r["resultado"] == "local":
            gana_local += 1
    ratio = gana_local / n
    assert ratio > 0.55, f"favorito ganó solo {ratio:.2%}"


def test_simetria_no_sesga_a_un_lado_por_codigo():
    """Cuotas simétricas → local y visita ganan ~parecido (sin sesgo del código)."""
    n = 3000
    gl = gv = 0
    for _ in range(n):
        r = simular_partido(2.6, 3.3, 2.6)  # local y visita igual de favoritos
        if r["resultado"] == "local":
            gl += 1
        elif r["resultado"] == "visita":
            gv += 1
    # Diferencia relativa pequeña (tolerancia amplia por aleatoriedad)
    assert abs(gl - gv) / n < 0.08, f"sesgo: local={gl} visita={gv}"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fallos = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            fallos += 1
            print(f"FAIL  {fn.__name__}: {e}")
    print(f"\n{len(fns) - fallos}/{len(fns)} tests OK")
    sys.exit(1 if fallos else 0)
