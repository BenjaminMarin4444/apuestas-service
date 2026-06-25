"""
auth.py — Validación del JWT EMITIDO POR casino-backend.

Este microservicio NO tiene login propio. Reutiliza el mismo token que el
frontend obtuvo de casino-backend, validándolo con el mismo `JWT_SECRET` y
algoritmo HS256. Del payload se extrae `sub` (id), `username` y `rol`.
"""
import os

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

JWT_SECRET = os.getenv("JWT_SECRET", "cambiame")
JWT_ALG = "HS256"

_bearer = HTTPBearer(auto_error=False)


def usuario_actual(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    if cred is None or not cred.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Falta token")
    try:
        # verify_sub=False: casino-backend firma `sub` como número (id de usuario)
        # y PyJWT 2.10+ exige que sea string. Desactivamos esa validación para
        # interoperar con el token tal cual lo emite el backend.
        payload = jwt.decode(
            cred.credentials, JWT_SECRET, algorithms=[JWT_ALG],
            options={"verify_sub": False},
        )
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido o expirado")
    return {
        "id": int(payload["sub"]),
        "username": payload.get("username"),
        "rol": payload.get("rol", "jugador"),
    }


def requiere_admin(usuario: dict = Depends(usuario_actual)) -> dict:
    if usuario.get("rol") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Requiere rol admin")
    return usuario
