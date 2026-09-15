"""Generate a plausible fleet and the telemetry its trackers send.

Two outputs. A vehicle registry, which in a real deployment comes from the fleet
management system and holds model, depot, rental status and contract. And a
tracker extract, which is what the telematics vendor delivers.

Every defect injected here is one somebody has hit in production: GPS modules
that report 0,0 before they acquire a fix, trackers refitted into another vehicle
without the records being updated, tank senders that send 255 when the wire is
off, and coolant sensors that send -999. Faults belong to specific units rather
than being sprayed across the fleet, because that is what a real extract looks
like and it is what makes "18 vehicles need attention" mean something.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sim import oem_feed
from zoneinfo import ZoneInfo

from sim.regions import REGIONS, SURNAMES, plate, vin

# The tracker platform is operated from Brazil and exports in its own local
# time, whatever region the vehicle is in. The mapping file declares the same
# zone, so the two have to agree or every reading lands hours out.
TRACKER_TZ = ZoneInfo("America/Sao_Paulo")

TELEMETRY_COLUMNS = [
    "placa", "data_hora", "data_recebimento", "latitude", "longitude",
    "altitude", "velocidade", "direcao", "odometro_km", "nivel_combustivel",
    "tensao_bateria", "ignicao", "temperatura_motor", "hdop", "satelites",
    "tipo_fix", "id_equipamento",
]

REGISTRY_COLUMNS = [
    "placa", "vin", "marca", "modelo", "categoria", "ano", "regiao",
    "deposito", "deposito_nome", "status", "contrato", "cliente_id", "cliente",
    "odometro_inicial", "id_equipamento",
]

FAULTS = {
    "gps_dead": 0.070,        # module never acquires a fix
    "tracker_swapped": 0.020,  # unit refitted into another vehicle
    "fuel_sender": 0.045,     # tank sender disconnected
    "coolant_wire": 0.025,    # coolant sensor unwired
    "bad_heading": 0.012,     # firmware sends compass points, not degrees
}

# How far a rented vehicle roams from its depot before heading back. Rentals run
# for days and cover real distance, so the radius is measured in hundreds of
# kilometres rather than the size of a metro area.
TETHER_DEGREES = 3.2


@dataclass
class SimVehicle:
    plate: str
    vin: str
    device: str
    region: str
    depot_code: str
    depot_name: str
    make: str
    model: str
    category: str
    year: int
    status: str
    contract: str
    customer_id: str
    customer: str
    home: tuple[float, float]
    lat: float
    lon: float
    heading: float
    odo: float
    fuel: float
    ignition: bool
    faults: set = field(default_factory=set)


def _step(lat, lon, heading_deg, distance_km):
    heading = math.radians(heading_deg)
    dlat = (distance_km / 111.32) * math.cos(heading)
    dlon = (distance_km / (111.32 * math.cos(math.radians(lat)))) * math.sin(heading)
    return lat + dlat, lon + dlon


def build_fleet(size: int, rng: random.Random, regions: list[str]) -> list[SimVehicle]:
    """Spread vehicles across the requested regions and their depots."""
    active = [REGIONS[code] for code in regions]
    depots = [(region, depot) for region in active for depot in region.depots]

    fleet: list[SimVehicle] = []
    for index in range(size):
        region, depot = depots[index % len(depots)]
        make, model, category = region.models[index % len(region.models)]

        roll = rng.random()
        status = "on_rent" if roll < 0.64 else ("available" if roll < 0.91 else "in_service")
        out = status == "on_rent"

        # Most rented cars are working near their depot and a long tail is away
        # on longer hires, which is what gives the map its shape. Cars on the
        # lot sit within a few hundred metres of it.
        spread = (rng.random() ** 2) * 3.0 if out else 0.004
        fleet.append(SimVehicle(
            plate=plate(region.code, rng),
            vin=vin(rng),
            device=f"TRK{800000 + index}",
            region=region.code,
            depot_code=depot.code,
            depot_name=depot.name,
            make=make, model=model, category=category,
            year=rng.choice([2022, 2023, 2023, 2024, 2024, 2025]),
            status=status,
            contract=f"AGR-{rng.randint(1000000, 9999999)}" if out else "",
            customer_id=f"CUST-{rng.randint(100000, 999999)}" if out else "",
            customer=(
                f"{rng.choice(SURNAMES[region.code])}, "
                f"{rng.choice('ABCDEFGHJKLMNPRSTW')}."
            ) if out else "",
            home=(depot.lat, depot.lon),
            lat=depot.lat + rng.uniform(-spread, spread),
            lon=depot.lon + rng.uniform(-spread, spread),
            heading=rng.uniform(0, 360),
            odo=rng.uniform(8_000, 140_000),
            fuel=rng.uniform(25, 95) if out else rng.uniform(8, 60),
            ignition=out and rng.random() < 0.75,
        ))

    for fault, share in FAULTS.items():
        for car in rng.sample(fleet, max(1, round(len(fleet) * share))):
            car.faults.add(fault)

    return fleet


def _advance(car: SimVehicle, seconds: int, rng: random.Random) -> float:
    """Move a vehicle on by one reporting interval. Returns its speed."""
    if car.status != "on_rent":
        return 0.0

    if rng.random() < 0.012:
        car.ignition = not car.ignition
    if not car.ignition:
        return 0.0

    speed = max(0.0, rng.gauss(52, 20))
    car.heading = (car.heading + rng.gauss(0, 12)) % 360

    # Rental vehicles work a metro area rather than wandering off across the
    # country, so steer back once a car drifts too far from its depot.
    home_lat, home_lon = car.home
    if math.hypot(car.lat - home_lat, car.lon - home_lon) > TETHER_DEGREES:
        car.heading = math.degrees(
            math.atan2(home_lon - car.lon, home_lat - car.lat)
        ) % 360

    travelled = speed * (seconds / 3600)
    car.lat, car.lon = _step(car.lat, car.lon, car.heading, travelled)
    car.odo += travelled
    car.fuel = max(3.0, car.fuel - travelled * 0.09)
    return speed


def _row(car: SimVehicle, when: datetime, speed: float, rng: random.Random) -> dict:
    local = when.replace(tzinfo=timezone.utc).astimezone(TRACKER_TZ)
    return {
        "placa": car.plate,
        "data_hora": local.strftime("%Y-%m-%d %H:%M:%S"),
        "data_recebimento": (
            local + timedelta(seconds=rng.randint(2, 40))
        ).strftime("%Y-%m-%d %H:%M:%S"),
        "latitude": f"{car.lat:.6f}",
        "longitude": f"{car.lon:.6f}",
        "altitude": f"{rng.uniform(5, 950):.1f}",
        "velocidade": f"{speed:.1f}",
        "direcao": f"{car.heading:.1f}",
        "odometro_km": f"{car.odo:.1f}",
        "nivel_combustivel": f"{car.fuel:.0f}",
        "tensao_bateria": f"{rng.uniform(12.1, 14.4):.1f}",
        "ignicao": "L" if car.ignition else "D",
        "temperatura_motor": (
            f"{rng.uniform(82, 97):.0f}" if car.ignition else f"{rng.uniform(10, 45):.0f}"
        ),
        "hdop": f"{rng.uniform(0.7, 2.4):.1f}",
        "satelites": str(rng.randint(6, 14)),
        "tipo_fix": "3d",
        "id_equipamento": car.device,
    }


def _inject(row: dict, car: SimVehicle, rng: random.Random) -> None:
    """Apply this vehicle's own faults, plus background comms noise."""
    if "gps_dead" in car.faults and rng.random() < 0.30:
        # Some firmware admits it has no fix. Plenty of units report 0,0 while
        # still claiming a 3d fix, which is why a position has to be checked as
        # a pair rather than trusted leaf by leaf.
        row["latitude"] = "0.000000"
        row["longitude"] = "0.000000"
        if rng.random() < 0.5:
            row["tipo_fix"] = "none"
            row["satelites"] = "0"
            row["hdop"] = "99.9"
        return

    if "tracker_swapped" in car.faults and rng.random() < 0.12:
        # The unit was pulled and refitted into another car, so this vehicle
        # appears to cross the country between two fixes seconds apart.
        region = REGIONS[car.region]
        far = region.depots[rng.randrange(len(region.depots))]
        row["latitude"] = f"{far.lat + rng.uniform(-0.5, 0.5):.6f}"
        row["longitude"] = f"{far.lon + rng.uniform(-0.5, 0.5):.6f}"
        return

    if "fuel_sender" in car.faults and rng.random() < 0.55:
        row["nivel_combustivel"] = "255"
        return

    if "coolant_wire" in car.faults and rng.random() < 0.40:
        row["temperatura_motor"] = "-999"
        return

    if "bad_heading" in car.faults and rng.random() < 0.35:
        row["direcao"] = rng.choice(["N", "NE", "S", "SW"])
        return

    roll = rng.random()
    if roll < 0.0012:
        row["placa"] = ""          # corrupt frame drops the registration
    elif roll < 0.0022:
        row["data_hora"] = ""      # clock on the unit was never set
    elif roll < 0.0028:
        row["nivel_combustivel"] = str(rng.randint(101, 120))


def generate(
    size: int,
    hours: float,
    interval_seconds: int,
    seed: int,
    regions: list[str],
    start: datetime | None = None,
):
    """Simulate the fleet once and observe it twice.

    One simulation holds the truth. The tracker and the OEM platform are two
    imperfect observers of it, which is what makes their disagreements real
    rather than arbitrary: both are describing the same car at the same moment
    through different equipment, at different cadences, with different lag.
    """
    rng = random.Random(seed)
    fleet = build_fleet(size, rng, regions)
    ticks = int(hours * 3600 / interval_seconds)
    if start is None:
        # End the window at the current time so the fleet reads as live, and so
        # rejected readings survive the landing zone's TTL.
        start = datetime.now(timezone.utc).replace(
            second=0, microsecond=0, tzinfo=None
        ) - timedelta(seconds=interval_seconds * ticks)

    connected = {car.plate for car in fleet if oem_feed.is_connected(car)}
    oem_every = max(1, oem_feed.REPORT_INTERVAL_SECONDS // interval_seconds)

    rows = []
    oem_rows = []
    for tick in range(ticks):
        when = start + timedelta(seconds=interval_seconds * tick)
        for car in fleet:
            speed = _advance(car, interval_seconds, rng)

            row = _row(car, when, speed, rng)
            _inject(row, car, rng)
            rows.append(row)

            # The OEM platform reports on its own slower schedule, and only for
            # vehicles whose manufacturer runs one.
            if car.plate in connected and tick % oem_every == 0:
                oem = oem_feed.row(car, when, speed, rng)
                if oem_feed.inject(oem, car, rng):
                    oem_rows.append(oem)

    rows.sort(key=lambda r: (r["placa"], r["data_hora"]))
    oem_rows.sort(key=lambda r: (r["vin"], r["event_time_utc"]))
    return rows, oem_rows, fleet


def registry_rows(fleet: list[SimVehicle]) -> list[dict]:
    return [
        {
            "placa": car.plate,
            "vin": car.vin,
            "marca": car.make,
            "modelo": car.model,
            "categoria": car.category,
            "ano": car.year,
            "regiao": car.region,
            "deposito": car.depot_code,
            "deposito_nome": car.depot_name,
            "status": car.status,
            "contrato": car.contract,
            "cliente_id": car.customer_id,
            "cliente": car.customer,
            "odometro_inicial": f"{car.odo:.0f}",
            "id_equipamento": car.device,
        }
        for car in fleet
    ]
