export const TALK_TRACK = [
  {
    heading: "Instructions and Talk Track",
    content: [
      {
        heading: "What this is",
        body:
          "Fleet telemetry arrives broken. Two feeds describe the same car in different words and different units, GPS modules report themselves in the Gulf of Guinea before they get a fix, tank senders send 255 when the wire is off, and trackers get moved between vehicles without anyone updating the records. No agent works until that is dealt with. This demo deals with it first, then builds fusion, graph, multimodal search and an agent on top, all on COVESA VSS 6.1.0 and MongoDB Atlas.",
      },
      {
        heading: "How to demo",
        body: [
          "Start on Overview. Four claims, each with the number that backs it and a way through to the screen that proves it. Work them in order; each step fails without the one before it.",
          "Step 1, Data quality. These numbers came from reading a vendor extract, with no database and no AI involved. The manufacturer feed rejects nothing. Switch to tracker_br and 53,918 of 6 million readings fail.",
          "Point at the map. The fleet works Brazil, North America and Europe, so the cluster sitting off West Africa is impossible. That is Null Island, latitude 0 longitude 0, where every uninitialised GPS module in the world reports itself to be.",
          "Click Null Island in the reasons list. The map flies to the Gulf of Guinea and worked examples appear underneath, each traceable to the record it came from.",
          "Click Impossible motion. A car covering 800 km in 30 seconds is a tracker that was moved into another vehicle, which is the cheapest theft and tow detector available.",
          "Open the VSS signals tab. The tree is read from vss.json: 1,382 signals across 354 branches, of which this demo maps 10. Press 'Show mapped only' to make the adoption argument in one click.",
          "Step 2, Fleet. Search a registration, click the row to fly the map, then press Detail and scroll to 'Which source was believed'. The manufacturer wins on fuel level because it reads the tank sender off the bus; the tracker wins on position because it has its own GNSS and reports far more often. Same two sources, opposite outcomes, and the arithmetic is on screen.",
          "Step 3, Damage. Search for 'shattered tail light', then click 'Show what vector search alone returned': it gives you four rear-end dents. The reranked list pulls genuine shattered glass up from rank 58. Vector search is recall, the cross encoder is precision, and the gap between them is the whole argument for the second pass.",
          "Then upload a photo. Claude reads the damage type, panel and severity off it, the image is embedded into the same space as the text, and the charge is quoted from the median of comparable settled claims rather than from a model's guess.",
          "Step 3, Recall. A supplier flags a brake pad batch and one traversal goes from the batch to 391 vehicles to 252 customers, in about 300 ms. Then read the amber banner: 35 of those cars cannot be located, because the data quality work already flagged their trackers as unbelievable. Leave that number hanging.",
          "Step 4, Agent. Run it. The trace labels every step, so the agent is never credited with work a query did. It scopes the recall, corroborates it against the damage claims you just searched, cohorts the 391 vehicles five ways, recalls this operator's urgency policy from memory, and proposes one action per cohort. Then it sends a small investigator after each vehicle nobody can locate.",
          "Finish on Approve. Nothing is written to the fleet: the approval is recorded against the run and every action stays a proposal.",
        ],
      },
      {
        heading: "The point to land",
        body: [
          "Nothing here was thrown away. Every rejection keeps its reason, so a firmware release that breaks a batch of trackers shows up as a reject rate that moves.",
          "None of the validation rules were written by hand. A coolant temperature of -999 C and a latitude of 91.0 fail against bounds that ship with the standard.",
          "The four steps are one chain, not four features. The agent can only say 'and it has already started' because the damage corpus exists, and it can only find the 391 vehicles because the graph does.",
          "Step 1 runs before any agent exists. Getting a report on a historical extract needs no live integration and no production access, which is why it is the step that actually happens.",
        ],
      },
    ],
  },
  {
    heading: "Behind the Scenes",
    content: [
      {
        heading: "The pipeline",
        body:
          "Collect, unify, validate, store, interpret, fuse, relate, retrieve, act. All of it is built. Vendor records are mapped onto VSS paths by a declarative YAML file, converted into the units the specification declares, put through a four level validation ladder, stored as time series and one document per vehicle, then fused where two sources overlap. On top of that sit the parts graph, multimodal retrieval over damage photographs, and a ReAct agent whose memory is three MongoDB collections.",
      },
      {
        heading: "The validation ladder",
        body: [
          "Level 1, value checks generated from vss.json: range, allowed set, format pattern, and vendor sentinels.",
          "Level 2, Null Island. Latitude 0 longitude 0 passes every per-leaf range check, so a position has to be checked as a pair rather than as two numbers.",
          "Level 3, the mobility test. Every coordinate can be individually valid while the path between two of them is impossible.",
          "Level 4, the service area. A position far outside the operating territory is flagged rather than silently trusted.",
        ],
      },
      {
        heading: "Fusion",
        body:
          "Two feeds describe the same car and disagree. A manufacturer connected platform identified by VIN, and an aftermarket tracker identified by registration, with different cadences, different lag and different units. Every candidate is scored as reliability times freshness decay times confidence, where reliability is per source and per signal. Every candidate is kept as provenance, so a disputed mileage charge is settled by looking rather than arguing.",
      },
      {
        heading: "Ontology and graph",
        body:
          "The ontology splits into a TBox, the blueprint of classes and relationships, and an ABox, the facts. The split is by rate of change: the blueprint moves when the domain does, the facts move constantly. Both live in MongoDB, the ABox as one typed edge collection traversed with $graphLookup. Edges that can change carry validFrom and validTo, because a replaced part and a tracker moved into another car both have to stay answerable afterwards. A traversal may only follow edge types the TBox confirms, which is what turns the ontology from documentation into a constraint on what an agent can ask.",
      },
      {
        heading: "Multimodal retrieval",
        body:
          "Damage photos are read by Claude on Bedrock into a fixed vocabulary of damage type, panel, severity and angle, because a label that cannot be used as an index filter is only decoration. voyage-multimodal-3 then embeds images and text into one space, which is what lets somebody search for what they mean rather than for what they have a picture of. The vector index carries filters alongside the vector, so a bumper photograph is never compared against wheel photographs. rerank-2.5 reorders the candidates, and on this corpus it routinely promotes the right answer from rank fifty to rank one.",
      },
      {
        heading: "The agent and its memory",
        body:
          "A ReAct loop on LangGraph, reasoning and acting until the procedure is done, with a Sonnet supervisor and Haiku investigators fanned out concurrently over the vehicles nobody can locate. Memory is three collections rather than a prompt: agent_procedures holds the standard operating procedure, so changing how the agent works is an edit to a document instead of a deploy; agent_taxonomy holds what this operator's words mean, including an urgency ladder with contractual response times that no model could infer; agent_episodes holds conclusions already reached, keyed to the state of the evidence they were drawn from, so a vehicle is not investigated twice. LangGraph's MongoDB checkpointer writes every reasoning step as it happens, which makes a run inspectable and resumable rather than a black box.",
      },
      {
        heading: "What the agent is not doing",
        body:
          "Scoping a recall is a graph traversal. Deciding which vehicles cannot be located is a field on a document. Neither needs a model, and the trace labels every step query, memory or decision so the agent is never credited with work the rules did. The judgement is the trade-off between customer risk and operational disruption, and the wording an operations team can act on. Everything goes to a human, and approval writes nothing to the fleet.",
      },
      {
        heading: "Why COVESA VSS",
        body:
          "VSS 6.1.0 gives 1,382 signals across 354 branches, each carrying a unit, a datatype, a sensor or actuator or attribute classification, and where relevant a range, an allowed set and a format pattern. Adopting it means the validation rules, the unit conversions and the vocabulary for the knowledge graph all arrive with the standard. This demo maps 10 of those signals, which is the honest scale of a first integration. Teams that skip it end up rebuilding it: a common signal model defined by name, datatype, unit and value is VSS, reached the long way round.",
      },
      {
        heading: "Adoption path",
        body: [
          "Step 0: clone and run on generated sample data. No account, no data, no meetings.",
          "Step 1: drop a historical extract in and write one column mapping file. Out comes this report, on your own fleet.",
          "Step 2: real adapters per vendor, which switches on trust scoring and fusion.",
          "Step 3: live streaming for one slice of the fleet.",
          "Step 4: your ontology, your questions, and your own SOP in agent_procedures.",
          "Everything that differs between operators lives in profiles/<name>/, so adopting this means adding a directory rather than forking the code.",
        ],
      },
      {
        heading: "Architecture",
        body:
          "FastAPI backend with the ingest pipeline kept pure, so it reads records and produces results without touching a database. Next.js and LeafyGreen UI on the front, with Leaflet and OpenStreetMap tiles for the map so there is no token to obtain. MongoDB Atlas holds the signal registry, the time series, the operational collections, the parts graph, the damage embeddings and the agent's memory and checkpoints. Embeddings and reranking run on Voyage AI, and reasoning runs on Claude through AWS Bedrock, orchestrated with LangGraph.",
      },
    ],
  },
];
