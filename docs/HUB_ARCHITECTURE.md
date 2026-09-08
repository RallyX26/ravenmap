# RavenMap architecture

Status: current after Stage 3H2. This document describes the implementation
that exists now, then records historical milestones and intentionally deferred
work. It is not a proposed replacement architecture.

## 1. Current architecture

RavenMap is a single-process, SQLite-backed HTTP application with explicit
route-family, authentication, ingest, privacy, and compatibility boundaries.
The current process still owns HTTP serving, application adapters, background
threads, caches, and filesystem-backed media.

The current route contract is characterized by:

- 57 documented GET routes;
- 36 documented POST routes;
- `public_mirror` route filtering remains part of the current contract;
- route names, payloads, statuses, authentication ordering, and privacy
  behavior preserved from the pre-extraction implementation.

The executable contract inventory is maintained by
`tools/test_hub_contract.py`; this document intentionally does not duplicate
the complete route table.

The characterized route names remain:

```text
GET /
/about /transparency /status /checksums /support /donate /business /ipcamera
/IPCamera /relay.py /download /api/download /hardware /build16 /help /app
/node /key /contribute /admin/bugs /drive /planes /api/aircraft /api/geocode
/api/scanner /api/places /api/heat /api/node/me /aim /rv /rv/mine /rv/pool
/rv/admin /api/rv/me /api/rv/queue /api/rv/contributed /rv/retracted
/api/rv/retracted /rv/photos /api/rv/held /api/rv/progress /api/rv/tokens
/api/health /api/policy /api/whoami /api/plate /api/stats /sw.js /login /review
/api/review/queue /api/pending /api/nodes /api/sightings /api/audit /api/live

POST /api/enroll /api/sightings /api/help/vote /api/node/progress
/api/node/label /api/bug /api/bug/close /api/bug/delete /api/node/whoami
/api/node/parked /api/node/key /api/node/span /api/node/confirm /api/heartbeat
/api/signals /api/sighting/fullres /api/heartbeat/bulk /api/review/edit
/api/report /api/review /api/key/qr /api/key/rotate /api/operator/login
/api/operator/logout /api/rv/login /api/rv/logout /api/rv/retracted/delete
/api/rv/held/fix /api/rv/verdict /api/drive/report /api/drive/vote
/api/rv/my-token /api/rv/tokens/new /api/rv/tokens/revoke /api/review/bulk
/api/purge
```

## 2. Composition root and HTTP layer

`hub.py` is the process entry point, HTTP composition root, and compatibility
surface. It is primarily responsible for:

- argument parsing, bind validation, database initialization, and listener
  startup;
- `ThreadingHTTPServer`/`BaseHTTPRequestHandler` integration;
- GET/POST/HEAD dispatch and route-family selection;
- response serialization, body draining, admission, cache coordination, and
  security-header plumbing;
- wiring extracted modules to the handler;
- startup of the janitor, optional simulator, and TLS listener;
- compatibility aliases for state and helpers moved to leaf modules.

It is not purely routing. Bounded domain and adapter logic remains inline for
some routes, including:

- `/api/nodes`;
- `/api/node/label`;
- signal observations;
- radar, sensor, aircraft, and air integrations;
- audit and other read adapters.

That remaining logic is accepted Stage 3 adapter/domain debt. It is not a
reason to perform another broad mechanical extraction before runtime and
storage decomposition.

The live heartbeat route delegates to `node_lifecycle.heartbeat`; the duplicate
legacy implementation was removed in Stage 3H2.

## 3. Major module boundaries

### HTTP and delivery support

- `transport.py` owns stateless body parsing, serialization helpers, errors,
  HEAD behavior, and route labels.
- `static.py` owns constrained static/snapshot file delivery.
- `tiles.py` owns tile allow-listing, upstream fetching, caching, and pruning.
- `pages.py` owns page shells, download probing, and page-specific adapters.
- `admission.py`, `ratelimit.py`, `microcache.py`, and `response_policy.py`
  own capacity, rate, cache, and response-policy state.

### Public and application route families

- `mapdata.py` owns public map reads and redacted projections.
- `community.py` owns help and drive compatibility routes.
- `operator_bugs.py` owns operator bug/report adapters.
- `operator_admin.py` owns operator token, purge, and administrative adapters.

These modules still receive the handler as an HTTP adapter seam and may call
named `db.*` operations. They do not import `hub.py`.

### Node routes and policy

- `node_lifecycle.py` owns heartbeat, bulk heartbeat, and setup progress.
- `node_self.py` owns node self-service reads and consent operations.
- `node_credentials.py` owns node keys, tokens, and full-resolution access.
- `node_auth.py` centralizes equivalent node authentication sequences.
- `ownership.py` keeps resource ownership separate from authentication.
- `node_label.py` contains label-specific persistence behavior.

### Reviewer and operator trust domains

- `reviewer_read.py` owns reviewer reads and queues.
- `reviewer_mutation.py` owns reviewer changes and verdicts.
- `review_auth.py` owns reviewer credential identification and conversion.
- `review_api.py` contains shared review operations.
- `operator_auth.py` owns the current operator check.
- `operator_admin.py` and `operator_bugs.py` provide operator route adapters.

### Vehicle ingest

- `ingest_decisions.py` owns classification and tier/candidate decisions.
- `ingest_record.py` owns timestamp normalization, privacy-safe coordinates,
  record construction, and response metadata.
- `ingest_media.py` owns snapshot, crop, evidence, and relay preparation.
- `ingest_persistence.py` owns candidate merge, insertion, post-insert writes,
  mirror handling, and feed publication.

## 4. Vehicle ingest architecture

The current flow is:

```text
HTTP ingest request
    |
    v
node authentication and signature verification
    |
    v
decision/classification policy
    |
    v
media and evidence preparation
    |
    v
timestamp, position, and record construction
    |
    v
persistence orchestration
    |
    v
merge/insert side effects, mirror writes, and live feed
    |
    v
HTTP response mapping
```

Preserved invariants include:

- candidate decisions precede forced-private hold semantics where applicable;
- privacy-safe coordinates enter the stored sighting record;
- review/evidence crops are prepared before mirror stripping where required;
- candidate merge and ordinary insert remain distinct paths;
- database, filesystem, and feed side effects are intentionally not one
  transaction;
- caller-controlled reviewed fields and `bank_ref` remain characterized
  inherited behavior rather than silently corrected.

`ingest_persistence.py` accepts an explicit store dependency while defaulting
to the current `db` implementation. This is a demonstrated ingest
substitution seam, not universal repository dependency injection.

## 5. Authentication and authorization boundaries

RavenMap has separate trust domains rather than one generic authorization API.

### Operator authority

The current operator model uses the existing operator authentication policy,
including loopback behavior when configured. The shared operator secret remains
a deferred security concern.

### Reviewer authority

Reviewer credentials, scopes, cookies/bearers, token provenance, and reviewer
route behavior remain owned by `review_auth.py`, `reviewer_read.py`, and
`reviewer_mutation.py`.

### Node/device authority

Node authentication preserves the ordered unknown-node, status, bearer, and
signature behavior. Some telemetry routes intentionally retain tokenless-node
compatibility; required-token routes remain distinct.

### Ownership

`ownership.py` answers whether a resource belongs to the authenticated node.
Authentication and ownership are intentionally separate decisions.

### Capability conversion

Node confirmation, node-to-reviewer credential conversion, and key rotation
are separate capability boundaries. They must not be collapsed into a generic
authorization helper.

## 6. Persistence architecture

### Current implementation

SQLite remains the deployment database, stored in the compatibility-preserved
`data/sparrow.db` path. `db.py` owns schema creation, migrations, named
operations, transaction behavior, and most application SQL.

Application modules generally call named `db.*` operations rather than issuing
raw SQL. Persistence characterization covers transaction-sensitive semantics,
merge boundaries, node statistics, review transitions, and retention behavior.

### Known exception

`privacy.purge_expired(conn)` remains the deliberate raw-connection boundary
for retention deletion. It is a retention/storage concern, not a Stage 3
repository redesign target.

### Storage limits

RavenMap does not yet provide universal repository dependency injection or a
PostgreSQL implementation. Node and reviewer modules may still import named
`db.*` functions directly. Filesystem-backed media remains part of the current
storage model.

### Deferred cycle

`nodes.py` imports `db.py`. `db.py` reaches node jitter/redaction behavior via
a lazy import in position handling. The cycle currently works and is coupled
to privacy-position semantics. Stage 4 storage/domain work is the likely owner;
it is not being changed during Stage 3.

## 7. Runtime and global state

The current process intentionally retains process-local assumptions:

- `ThreadingHTTPServer` and per-connection handler instances;
- SQLite thread-local connections and schema-ready state;
- in-memory feed subscribers for SSE;
- microcache, tile cache, rate-limit, and admission state;
- tile-fetch semaphores and single-flight coordination;
- static police/radar/live-data caches;
- global media paths derived from `core.DATA`;
- daemon janitor, simulator, and TLS-listener threads;
- external road, geocoder, tile, aircraft, and publication integrations.

These are current implementation facts, not a claim that the process is
distributed-safe. Stage 4 is expected to separate API serving, workers,
schedulers, singleton jobs, and storage lifecycles. Stage 3 does not redesign
these globals or threads.

## 8. RavenMap fork and compatibility boundaries

Active configuration uses Raven-native names first:

```text
RAVEN_HUB
    -> SPARROW_HUB
    -> fail closed

RAVEN_REPO
    -> SPARROW_REPO
    -> fail closed

RAVEN_BIND
    -> SPARROW_BIND
    -> existing :: default

RAVEN_HEALTH_URL
    -> SPARROW_HEALTH_URL
    -> skip/unconfigured

RAVEN_STATS_ORIGIN
    -> SPARROW_STATS_ORIGIN
    -> no CORS allow-origin header
```

Other active operational aliases follow the same Raven-first compatibility
rule, including alert repository, box, key, sources, interval, and worker
settings. Legacy names remain supported; this document does not declare them
deprecated.

Intentionally retained identifiers include:

- `sparrow.db`, `/opt/sparrowmap`, existing service/unit names and installed
  paths;
- `sparrow_op`, `sparrow_rv`, and legacy browser storage/session names;
- legacy `SPARROW_*` environment variables;
- legacy asset and installer filenames such as `sparrow-app.js`;
- Sparrow Send, `sparrowsend`, and associated protocol identifiers;
- upstream attribution, history, licensing, and explicitly labeled source
  links.

These remaining names are compatibility, protocol, persistence, or provenance
surfaces, not forgotten RavenMap product branding.

## 9. Fork and provenance treatment

RavenMap is a fork/continuation derived from SparrowMap. Upstream attribution,
history, and licensing context remain part of the project. RavenMap now has
independent operational hub, repository, bind, health, and deployment
configuration.

Explicit upstream-only tools and historical links may remain. RavenMap-owned
clients, installers, and deployment paths must not silently contact, clone,
redirect to, or mutate SparrowMap infrastructure by default.

## 10. Known deferred findings

| Finding | Current status | Future owner |
|---|---|---|
| `db.py` <-> `nodes.py` lazy cycle | Working; coupled to position privacy | Stage 4 storage/domain |
| `privacy.purge_expired(conn)` | Deliberate raw-connection retention boundary | Retention/storage phase |
| `/api/audit` authentication behavior | Inherited behavior remains characterized | Future security phase |
| Tokenless-node compatibility | Preserved for existing clients | Future security/compatibility |
| Status-before-auth disclosures | Preserved ordering and responses | Future security phase |
| Shared operator secret | Current model retained | Future security phase |
| Reviewer cookie/bearer model | Current model retained | Future security phase |
| Node-to-reviewer bridge | Explicit capability conversion | Future security phase |
| Key-rotation audit omission | Known audit gap | Future security phase |
| Device-token storage hardening | Existing storage model retained | Future security phase |
| WebAuthn/passkeys | Not implemented | Future security phase |
| VAPID contact identity | Sparrow contact metadata retained; no Raven domain invented | Deployment/infrastructure |
| Filesystem-backed media | Current storage model | Stage 4 storage |
| SQLite and process-global state | Current deployment constraint | Stage 4 runtime/storage |
| Janitor, simulator, TLS, feed ownership | Daemon-thread model retained | Stage 4 runtime |
| Windows listener reuse issue | Environmental/platform concern | Platform maintenance |
| Sparrow compatibility identifiers | Intentionally retained | Compatibility maintenance |

## 11. Dependency direction

The intended production direction is:

```text
hub.py
    |
    v
route and application adapters
    |
    v
policy, domain, persistence, and storage helpers
```

No production module imports `hub.py`. Test suites may intentionally import
the composition root to exercise real HTTP behavior and compatibility aliases.
The graph is not cycle-free because the `db.py`/`nodes.py` cycle remains
documented and deferred.

## 12. Historical refactor milestones

The following milestones describe completed work and are not pending review
instructions:

- **Stage 0:** inventoried `hub.py`, route contracts, security boundaries, and
  externally observable HTTP behavior.
- **Stage 1A/1B:** extracted stateless transport primitives and stateful
  transport infrastructure while preserving behavior.
- **Stage 2:** extracted page/static, public map, community, node, reviewer,
  and operator route families incrementally.
- **Stage 3A-3D:** extracted vehicle decisions, records, media, and persistence
  orchestration.
- **Stage 3E:** centralized equivalent node authentication and separated
  ownership predicates while preserving distinct route policies.
- **Stage 3F:** characterized persistence semantics, closed most application
  raw-SQL leaks, and added the injectable ingest persistence seam.
- **Stage 3G:** severed silent operational SparrowMap defaults, added
  Raven-native configuration aliases, refreshed product branding, and
  documented intentional compatibility/provenance names.
- **Stage 3H1:** completed structural closeout analysis.
- **Stage 3H2:** characterized the live heartbeat delegation and removed the
  unreachable duplicate heartbeat implementation.

Historical findings remain useful when they explain why a current boundary or
compatibility rule exists. Earlier proposed target graphs and review gates are
superseded by the current-state sections above.

## 13. Stage 3 outcome and Stage 4 handoff

Stage 3 transformed RavenMap from a hub-centric monolithic application into a
composition-root architecture with explicit ingest, authentication, ownership,
persistence, route-family, and fork-compatibility boundaries while preserving
inherited behavior.

The remaining work is not another broad file-extraction exercise. Stage 4
should address runtime lifecycle, worker/scheduler separation, storage
boundaries, filesystem media, SQLite/process-global assumptions, and the
deferred security findings assigned above.
