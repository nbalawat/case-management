"""Demo personas. A real deployment federates to the client's identity provider instead."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    role: str
    title: str


PERSONAS: dict[str, Persona] = {
    p.id: p
    for p in [
        Persona("ana", "Ana Ruiz", "analyst", "L1 alert analyst"),
        Persona("ivan", "Ivan Petrov", "investigator", "L2 investigator"),
        Persona("maya", "Maya Chen", "mlro", "MLRO / BSA officer"),
        Persona("omar", "Omar Haddad", "admin", "Platform admin"),
    ]
}


def get_persona(pid: str | None) -> Persona:
    return PERSONAS.get(pid or "", PERSONAS["ivan"])
