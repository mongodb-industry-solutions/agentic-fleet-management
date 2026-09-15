"""The ontology: a TBox describing the domain, and an ABox of facts.

The split is by rate of change. The TBox is the blueprint, designed once per
version and revised when the domain changes. The ABox is the instances, loaded
every time data arrives. Keeping them apart is what lets the schema be small
enough to reason about while the facts run to millions.

Both live in MongoDB. The TBox is a collection of concept documents, each
recording the collection it is sourced from, so a relationship can always be
traced back to the data that produced it. The ABox is a single typed edge
collection traversed with $graphLookup.

The TBox does double duty. It answers "what can be asked", and it constrains what
an agent is allowed to ask: a traversal may only use edge types the TBox
confirms, so a question about a relationship nobody modelled produces a refusal
rather than a plausible invention.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

TBOX = "tbox"
EDGES = "edges"
BUSINESS_QUESTIONS = "business_questions"

# Namespaces keep node ids unambiguous across entity kinds, so an edge can point
# at anything without a separate type column.
NAMESPACES = {
    "Vehicle": "vehicle",
    "Supplier": "supplier",
    "Lot": "lot",
    "Part": "part",
    "Agreement": "agreement",
    "Customer": "customer",
    "Depot": "depot",
    "Model": "model",
    "Device": "device",
}


def node_id(kind: str, value: str) -> str:
    return f"{NAMESPACES[kind]}:{value}"


@dataclass(frozen=True)
class OntologyClass:
    name: str
    label: str
    description: str
    source_collection: str
    key_field: str
    attributes: tuple[str, ...] = ()

    @property
    def uri(self) -> str:
        return f"fleet:{self.name}"

    def to_document(self) -> dict:
        return {
            "_id": self.uri,
            "kind": "class",
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "source": {"collection": self.source_collection, "key": self.key_field},
            "attributes": list(self.attributes),
            "namespace": NAMESPACES.get(self.name),
            # What schema vector search embeds. A class is found by what it means
            # rather than by what it is called.
            "text": f"{self.label}. {self.description}",
        }


@dataclass(frozen=True)
class OntologyRelationship:
    name: str
    label: str
    description: str
    subject: str
    object: str
    time_bounded: bool = False
    inverse: str | None = None

    @property
    def uri(self) -> str:
        return f"fleet:{self.name}"

    def to_document(self) -> dict:
        return {
            "_id": self.uri,
            "kind": "relationship",
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "subject": f"fleet:{self.subject}",
            "object": f"fleet:{self.object}",
            "edgeType": self.name,
            "timeBounded": self.time_bounded,
            "inverse": self.inverse,
            "text": (
                f"{self.label}. {self.description} "
                f"Connects a {self.subject} to a {self.object}."
            ),
        }


CLASSES: tuple[OntologyClass, ...] = (
    OntologyClass(
        "Vehicle", "Vehicle",
        "A road vehicle in the fleet, identified by its registration. Carries "
        "live signal state fused from every source that reports on it.",
        "asset_shadow", "_id",
        ("make", "model", "year", "status", "depot", "region"),
    ),
    OntologyClass(
        "Supplier", "Component supplier",
        "A manufacturer that supplies components to the fleet, and the party a "
        "recall or safety advisory originates from.",
        "suppliers", "_id", ("name", "country"),
    ),
    OntologyClass(
        "Lot", "Production batch",
        "A batch of one component type produced by one supplier in one run. "
        "Recalls are issued against a batch rather than an individual part, "
        "which is why a batch is the unit that matters.",
        "lots", "_id",
        ("partType", "criticality", "manufacturedOn", "quantity", "unitCost"),
    ),
    OntologyClass(
        "Part", "Fitted component",
        "One physical component fitted to one vehicle, traceable to the batch "
        "it came from and the date it was fitted.",
        "parts", "_id", ("partType", "position", "fittedOn"),
    ),
    OntologyClass(
        "Agreement", "Rental agreement",
        "A contract putting a vehicle in a customer's hands for a period. An "
        "open agreement is what turns a recall into a phone call.",
        "asset_shadow", "registry.contrato", ("status",),
    ),
    OntologyClass(
        "Customer", "Customer",
        "The person or company currently renting a vehicle, and the party who "
        "has to be contacted when something is wrong with it.",
        "asset_shadow", "registry.cliente", ("name",),
    ),
    OntologyClass(
        "Depot", "Depot",
        "A branch or airport location where vehicles are collected, returned "
        "and serviced.",
        "asset_shadow", "registry.deposito", ("name", "region"),
    ),
    OntologyClass(
        "Model", "Vehicle model",
        "A make and model combination. Faults and recalls often follow a model "
        "or a platform rather than an individual vehicle.",
        "asset_shadow", "registry.modelo", ("make", "category"),
    ),
    OntologyClass(
        "Device", "Telematics device",
        "A tracker fitted to a vehicle. The link is time bounded because units "
        "get moved between vehicles.",
        "asset_shadow", "registry.id_equipamento", ("vendor",),
    ),
)


RELATIONSHIPS: tuple[OntologyRelationship, ...] = (
    OntologyRelationship(
        "SUPPLIES", "supplies",
        "The supplier that produced a production batch.",
        "Supplier", "Lot", inverse="SUPPLIED_BY",
    ),
    OntologyRelationship(
        "CONTAINS", "contains",
        "A production batch contains the individual components cut from it.",
        "Lot", "Part", inverse="FROM_LOT",
    ),
    OntologyRelationship(
        "FITTED_TO", "fitted to",
        "A component is fitted to a vehicle on a date. Time bounded, because a "
        "replacement must not erase the fact that the old part was once there.",
        "Part", "Vehicle", time_bounded=True, inverse="HAS_PART",
    ),
    OntologyRelationship(
        "RENTED_UNDER", "rented under",
        "A vehicle is out on a rental agreement. Time bounded, since a vehicle "
        "passes through many agreements.",
        "Vehicle", "Agreement", time_bounded=True,
    ),
    OntologyRelationship(
        "HELD_BY", "held by",
        "The customer who holds a rental agreement.",
        "Agreement", "Customer",
    ),
    OntologyRelationship(
        "HOMED_AT", "homed at",
        "The depot a vehicle belongs to.",
        "Vehicle", "Depot",
    ),
    OntologyRelationship(
        "OF_MODEL", "of model",
        "The model a vehicle is an instance of.",
        "Vehicle", "Model",
    ),
    OntologyRelationship(
        "TRACKED_BY", "tracked by",
        "The telematics device reporting on a vehicle. Time bounded, because a "
        "unit refitted into another car is the cause of most identity errors.",
        "Vehicle", "Device", time_bounded=True,
    ),
)


# The questions the ontology exists to answer. Following UMOS ONE's approach,
# relationships are added because a question needs them rather than by mapping
# the schema exhaustively, so this list is the specification for the TBox.
QUESTIONS = [
    {
        "_id": "recall-blast-radius",
        "question": "A supplier has flagged a production batch. Which vehicles "
                    "carry a part from it, which of those are out on rent right "
                    "now, and which customers do we have to call?",
        "traversal": ["SUPPLIES", "CONTAINS", "FITTED_TO", "RENTED_UNDER", "HELD_BY"],
        "hops": 5,
        "withoutOntology": "Five collections and four joins, rewritten for every "
                           "variation of the question.",
        "value": "Turns a safety advisory into a call list in one query.",
    },
    {
        "_id": "recall-exposure-by-region",
        "question": "For that same batch, how is the exposure split across "
                    "regions and depots?",
        "traversal": ["CONTAINS", "FITTED_TO", "HOMED_AT"],
        "hops": 3,
        "withoutOntology": "A separate reporting query against the parts system.",
        "value": "Decides where to send replacement stock.",
    },
    {
        "_id": "supplier-exposure",
        "question": "How much of the fleet depends on a single supplier for "
                    "safety-critical components?",
        "traversal": ["SUPPLIES", "CONTAINS", "FITTED_TO"],
        "hops": 3,
        "withoutOntology": "Nobody asks it, because it is too awkward to answer.",
        "value": "Concentration risk before it becomes an incident.",
    },
    {
        "_id": "device-history",
        "question": "Which vehicle was this tracker fitted to on a given date?",
        "traversal": ["TRACKED_BY"],
        "hops": 1,
        "withoutOntology": "A spreadsheet somebody forgot to update.",
        "value": "Settles a disputed mileage charge.",
    },
    {
        "_id": "model-fault-concentration",
        "question": "Which vehicle models are producing the most data quality "
                    "findings, and do they share a supplier?",
        "traversal": ["OF_MODEL", "HAS_PART", "FROM_LOT", "SUPPLIED_BY"],
        "hops": 4,
        "withoutOntology": "Two analysts and a week.",
        "value": "Finds a bad batch before the supplier admits to one.",
    },
]


@dataclass
class Tbox:
    """The blueprint, and the allowlist an agent is held to."""

    classes: tuple[OntologyClass, ...] = CLASSES
    relationships: tuple[OntologyRelationship, ...] = RELATIONSHIPS
    questions: list[dict] = field(default_factory=lambda: QUESTIONS)

    @property
    def edge_types(self) -> set[str]:
        types = {r.name for r in self.relationships}
        types |= {r.inverse for r in self.relationships if r.inverse}
        return types

    def documents(self) -> list[dict]:
        return [c.to_document() for c in self.classes] + [
            r.to_document() for r in self.relationships
        ]

    def confirm(self, edge_types: list[str]) -> tuple[list[str], list[str]]:
        """Split requested edge types into those the TBox knows and those it does not.

        This is the guardrail. A traversal is only permitted over relationships
        the ontology confirms, so an agent asking about a relationship nobody
        modelled gets a refusal instead of a fabricated answer.
        """
        known = self.edge_types
        allowed = [t for t in edge_types if t in known]
        unknown = [t for t in edge_types if t not in known]
        return allowed, unknown

    def summary(self) -> dict:
        return {
            "classes": len(self.classes),
            "relationships": len(self.relationships),
            "questions": len(self.questions),
            "edgeTypes": sorted(self.edge_types),
        }
