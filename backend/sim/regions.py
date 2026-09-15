"""Where a fleet operates, and what its vehicles and plates look like there.

Leafy Mobility runs across three regions because the operators this is built for
do too: a Brazilian rental company, a North American mobility group, and OEMs
with fleets in Europe. Keeping region a data structure rather than a hardcoded
assumption is what lets a customer profile replace it wholesale.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Depot:
    code: str
    name: str
    lat: float
    lon: float
    region: str


@dataclass(frozen=True)
class Region:
    code: str
    name: str
    locale: str
    timezone: str
    depots: tuple[Depot, ...]
    models: tuple[tuple[str, str, str], ...]
    distance_unit: str = "km"


BRAZIL = Region(
    code="BR",
    name="Brazil",
    locale="pt-BR",
    timezone="America/Sao_Paulo",
    depots=(
        Depot("GRU", "Sao Paulo Guarulhos", -23.4356, -46.4731, "BR"),
        Depot("CGH", "Sao Paulo Congonhas", -23.6266, -46.6554, "BR"),
        Depot("GIG", "Rio Galeao", -22.8100, -43.2506, "BR"),
        Depot("CNF", "Belo Horizonte Confins", -19.6336, -43.9686, "BR"),
        Depot("CWB", "Curitiba Afonso Pena", -25.5285, -49.1758, "BR"),
        Depot("BSB", "Brasilia Internacional", -15.8697, -47.9208, "BR"),
        Depot("POA", "Porto Alegre Salgado Filho", -29.9939, -51.1711, "BR"),
        Depot("SSA", "Salvador Deputado Magalhaes", -12.9086, -38.3225, "BR"),
    ),
    models=(
        ("Fiat", "Cronos", "sedan"), ("Fiat", "Argo", "hatch"),
        ("Fiat", "Mobi", "hatch"), ("Volkswagen", "Polo", "hatch"),
        ("Volkswagen", "T-Cross", "suv"), ("Chevrolet", "Onix", "hatch"),
        ("Chevrolet", "Tracker", "suv"), ("Hyundai", "HB20", "hatch"),
        ("Renault", "Kwid", "hatch"), ("Toyota", "Corolla", "sedan"),
        ("Jeep", "Renegade", "suv"), ("Nissan", "Kicks", "suv"),
    ),
)

NORTH_AMERICA = Region(
    code="NA",
    name="North America",
    locale="en-US",
    timezone="America/Chicago",
    distance_unit="mi",
    depots=(
        Depot("DFW", "Dallas Fort Worth", 32.8998, -97.0403, "NA"),
        Depot("ATL", "Atlanta Hartsfield", 33.6407, -84.4277, "NA"),
        Depot("ORD", "Chicago O'Hare", 41.9742, -87.9073, "NA"),
        Depot("LAX", "Los Angeles International", 33.9416, -118.4085, "NA"),
        Depot("MCO", "Orlando International", 28.4312, -81.3081, "NA"),
        Depot("DEN", "Denver International", 39.8561, -104.6737, "NA"),
        Depot("PHX", "Phoenix Sky Harbor", 33.4342, -112.0116, "NA"),
        Depot("YYZ", "Toronto Pearson", 43.6777, -79.6248, "NA"),
    ),
    models=(
        ("Toyota", "Camry", "sedan"), ("Toyota", "RAV4", "suv"),
        ("Chevrolet", "Malibu", "sedan"), ("Chevrolet", "Equinox", "suv"),
        ("Ford", "Escape", "suv"), ("Ford", "F-150", "truck"),
        ("Nissan", "Altima", "sedan"), ("Nissan", "Rogue", "suv"),
        ("Hyundai", "Elantra", "sedan"), ("Jeep", "Grand Cherokee", "suv"),
        ("Kia", "Telluride", "suv"), ("Tesla", "Model 3", "sedan"),
    ),
)

EUROPE = Region(
    code="EU",
    name="Europe",
    locale="en-GB",
    timezone="Europe/London",
    depots=(
        Depot("LHR", "London Heathrow", 51.4700, -0.4543, "EU"),
        Depot("CDG", "Paris Charles de Gaulle", 49.0097, 2.5479, "EU"),
        Depot("FRA", "Frankfurt am Main", 50.0379, 8.5622, "EU"),
        Depot("MAD", "Madrid Barajas", 40.4983, -3.5676, "EU"),
        Depot("MXP", "Milan Malpensa", 45.6301, 8.7255, "EU"),
        Depot("AMS", "Amsterdam Schiphol", 52.3105, 4.7683, "EU"),
        Depot("MUC", "Munich Franz Josef Strauss", 48.3538, 11.7861, "EU"),
        Depot("LIS", "Lisbon Humberto Delgado", 38.7742, -9.1342, "EU"),
    ),
    models=(
        ("Volkswagen", "Golf", "hatch"), ("Volkswagen", "ID.4", "suv"),
        ("Renault", "Clio", "hatch"), ("Peugeot", "2008", "suv"),
        ("Skoda", "Octavia", "sedan"), ("Opel", "Corsa", "hatch"),
        ("BMW", "1 Series", "hatch"), ("Mercedes-Benz", "A-Class", "hatch"),
        ("Fiat", "500e", "hatch"), ("Seat", "Leon", "hatch"),
        ("Ford", "Puma", "suv"), ("Citroen", "C3", "hatch"),
    ),
)

REGIONS = {r.code: r for r in (BRAZIL, NORTH_AMERICA, EUROPE)}

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
VIN_ALPHABET = "0123456789ABCDEFGHJKLMNPRSTUVWXYZ"


def plate(region_code: str, rng: random.Random) -> str:
    """Registration in the local format, which is what an operator recognises."""
    letters = lambda n: "".join(rng.choice(LETTERS) for _ in range(n))  # noqa: E731

    if region_code == "BR":  # Mercosul: ABC1D23
        return f"{letters(3)}{rng.randint(0, 9)}{rng.choice(LETTERS)}{rng.randint(0, 99):02d}"
    if region_code == "NA":  # North American style: ABC-1234
        return f"{letters(3)}-{rng.randint(1000, 9999)}"
    return f"{letters(2)}{rng.randint(10, 99)} {letters(3)}"  # UK style: AB12 CDE


def vin(rng: random.Random) -> str:
    """17 characters with no I, O or Q, matching the pattern VSS declares."""
    return (
        "".join(rng.choice(VIN_ALPHABET[10:]) for _ in range(3))
        + "".join(rng.choice(VIN_ALPHABET) for _ in range(6))
        + "".join(rng.choice(VIN_ALPHABET) for _ in range(4))
        + f"{rng.randint(0, 9999):04d}"
    )


SURNAMES = {
    "BR": ["Silva", "Santos", "Oliveira", "Souza", "Lima", "Pereira", "Costa",
           "Rodrigues", "Almeida", "Nascimento", "Carvalho", "Araujo"],
    "NA": ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
           "Davis", "Martinez", "Wilson", "Anderson", "Taylor"],
    "EU": ["Muller", "Dubois", "Rossi", "Garcia", "Novak", "Jansen", "Andersson",
           "Kowalski", "Silva", "Murphy", "Schmidt", "Bianchi"],
}


def all_depots() -> list[Depot]:
    return [d for region in REGIONS.values() for d in region.depots]
