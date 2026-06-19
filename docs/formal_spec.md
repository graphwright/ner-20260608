# Principles of Reliable Reasoning: Formal Definition

This defines the formal model precisely, establishes vocabulary, states hard rules,
and lists explicit non-goals. When in doubt, check against this file before generating
code, prose, or schema definitions.

The notation itself isn't the point — the benefits come from what the process of
formalizing forces, and those benefits survive translation into plain prose.

**It settles ambiguity permanently.** Natural language descriptions of data structures
always leave wiggle room. "Edges have types" could mean a dozen things. A formal
definition closes off all of them at once. Even if readers never see the notation,
the author has made decisions that make every subsequent explanation consistent.

**It separates schema from instance.** The $T$ vs. $V$ split is the single most
important conceptual distinction in the book. A formal definition makes it impossible
to conflate the two.

**It gives you a checklist.** The 4-tuple is a completeness check. If you can't place
something in one of those slots, either it doesn't belong in the model or the model
is missing a slot. That discipline propagates into the code, the explanations, and
the worked examples.

**It anchors the vocabulary.** Once you've defined $\text{Tr}(p)$ formally, "trait"
has a precise meaning for the rest of the book.

**It makes identity unambiguous.** Two instances that represent the same real-world
entity must be distinguishable from two instances that represent the same claim at
different epistemic states. Canonical IDs close off that confusion: each member of
$V$ has a stable, opaque identifier assigned at construction and never derived from
its content. Merging, deduplication, and cross-reference all become well-defined
operations.

**It grounds every claim in its source.** A proposition without provenance is not
reliable knowledge — it is an unverifiable assertion. When every predicate instance
carries a declared provenance sub-schema, tracing any fact back to its origin is a
field lookup, not a search. That discipline propagates into ingestion pipelines,
dispute resolution, and epistemic audits.

---

## Formal Definition

A typed graph $G$ is a 4-tuple $\mathbf{(T,\ \Phi,\ V,\ \tau)}$ where:

### Schema layer — fixed at graph-design time

- $T$ — finite set of **types**, partitioned into:
  * $T_\text{ent}$ — **entity types** (e.g. Person, Drug, Location)
  * $T_\text{pred}$ — **predicate types** (e.g. Treats, KnewAt, LocatedIn)
- $\Phi: T \to \text{FieldSchema}$ — the **field schema**, mapping each type to a
  Pydantic model declaration of named, typed fields. For predicate types,
  $\Phi$ includes three distinguished fields:
  * `subject` — typed reference to an instance in $V$; the type annotation
    constitutes $\text{dom}(p)$
  * `object_` — typed reference to an instance in $V$; the type annotation
    constitutes $\text{ran}(p)$
  * `truth_status` — the graph's current commitment to the proposition
    this instance expresses
- For each $p \in T_\text{pred}$:
  * $\text{dom}(p) \subseteq T$ — permitted subject types, read from the type
    annotation of `subject` in $\Phi(p)$
  * $\text{ran}(p) \subseteq T$ — permitted object types, read from the type
    annotation of `object_` in $\Phi(p)$
  * $\text{Tr}(p) \subseteq \text{Trait}$ — finite set of semantic traits

The partition is strict: $T_\text{ent} \cap T_\text{pred} = \emptyset$ and
$T_\text{ent} \cup T_\text{pred} = T$. Every type is exactly one of the two;
$\Phi$ determines which, by whether it declares the distinguished fields
`subject`, `object_`, and `truth_status`. Note the asymmetry with the instance
layer: every predicate *instance* is a full member of $V$ ($E \subseteq V$),
but no predicate *type* is an entity *type*. The Python realization mirrors
both facts at once: `EntityInstance` and `BaseStatement` are disjoint siblings
under a common root class `Instance`, which carries membership in $V$ (the
`id` field). $\tau$ assigns each instance its most-derived class, which falls
unambiguously on one side of the partition.

The field schema $\Phi$ is the central structural definition. It defines what each
type IS — including whether a type is a predicate (carries subject/object/truth_status)
or a plain entity (does not). Domain and range constraints, truth semantics, and
field validation all derive from $\Phi$. The graph topology is emergent: it is
recovered by reading the subject/object fields of predicate-typed instances.

### Instance layer — populated at ingestion or reasoning time

- $V$ — set of all **instances** (both entity instances and predicate instances)
- $\tau: V \to T$ — type assignment for all instances

The **edge set** $E$ is derived, not primitive:

$$
E = \{v \in V : \tau(v) \in T_\text{pred}\}
$$

$E \subseteq V$, that is,
every member of $E$ is also a member of $V$. A predicate instance is a full
member of $V$ — it has an id, it can be referenced by other predicate instances
as their subject or object. This is the single relaxation relative to the
classical graph formalism, where $V$ and $E$ are disjoint sorts. It is what
enables higher-order predication without a separate reification mechanism.

### Canonical identity

Each instance $v \in V$ carries a distinguished field $v.\text{id} \in \mathcal{I}$,
where $\mathcal{I}$ is an opaque universe of identifiers. The identity axiom requires:

$$\forall\, v, v' \in V:\ v \neq v' \Rightarrow v.\text{id} \neq v'.\text{id}$$

That is, $\text{id}$ is injective on $V$. The identifier is:

- **Assigned at construction** — not derived from any mutable field or external state
- **Stable** — once assigned, it does not change (consistent with R7)
- **Opaque** — it carries no type information; type is the exclusive responsibility of $\tau$
- **Ontology-anchored where possible** — for entity instances that correspond to
  real-world referents, the id should be sourced from or aligned with a community-curated
  authoritative ontology (e.g. Wikidata QIDs, MeSH IDs, a domain-specific authority
  such as Baker Street Wiki). Minting ad-hoc IDs for named entities that have
  established canonical IDs elsewhere is a traceability failure.

In Python, `Instance.id` is a `str` field (typically a UUID or an ontology-authority
ID) declared before any domain-specific fields and frozen by the model configuration.

### Validity constraint

Each instance $v \in V$ carries fields conforming to $\Phi(\tau(v))$.

For predicate instances, this subsumes domain/range enforcement: if $\Phi(p)$
declares `subject: Drug` and `object_: Disease`, then an instance of type $p$
whose subject is a Location fails field validation. No separate domain/range
check is needed.

### Trait vocabulary

$$
\text{Trait} ::= \text{Symmetric} \mid \text{Transitive} \mid \text{Functional} \mid \text{InverseFunctional} \mid \text{Inverse}(p') \mid \text{Rule}(\phi \Rightarrow \psi)
$$

Traits are realized as Python mixin classes inherited alongside the base predicate
class. The unparameterized traits (`Symmetric`, `Transitive`, `Functional`,
`InverseFunctional`) are plain marker classes. `Inverse` is a generic parameterized
by the partner predicate type. `Rule` has no clean type-level expression and is
documented in prose on the predicate class.

### Truth status

Every predicate instance carries a `truth_status` field:

$$
\text{TruthStatus} ::= \text{asserted\\_true} \mid \text{asserted\\_false} \mid \text{hypothetical} \mid \text{disputed} \mid \text{retracted}
$$

Under the closed-world assumption, the presence of a predicate instance does NOT
by itself assert the proposition; the `truth_status` field carries the assertion
explicitly. This replaces the classical convention where edge-presence is assertion.

The **asserted graph** — the first-order fact graph available for traversal — is
the projection of $E$ where `truth_status = asserted_true`. A disputed proposition
remains in $V$ (it can be referenced, queried, and reasoned about) but is excluded
from the asserted graph.

Lifecycle: a predicate instance is typically created as `hypothetical` at first
mention, promoted to `asserted_true` when grounded, and may later become `disputed`
(conflicting sources) or `retracted` (overturned by new evidence).

### Provenance

Every predicate instance carries a **provenance sub-schema** — a distinguished set
of fields in $\Phi(p)$ that record how the assertion was produced. The minimum
provenance fields required for any $p \in T_\text{pred}$ are:

- `source` — the origin of the claim: a text span, document reference, or extraction
  method identifier
- `extraction_method` — how the claim was derived: direct quotation, inference, model
  extraction, manual annotation

Additional provenance fields may be declared per predicate type in $\Phi(p)$. The
field `narrator_confidence` in the Holmes schema is one such extension.

Provenance fields are instance metadata (R2): they describe how this particular
assertion was produced, not what the predicate type means. The distinction matters:
two instances of the same predicate type may have identical `subject`, `object_`, and
`truth_status` and differ only in provenance. Both are valid, distinct members of $V$
— their ids differ.

**Provenance does not replace truth_status.** A claim with high-confidence provenance
from a reliable source may still be `disputed` (if contradicted) or `retracted` (if
overturned). Truth status is the graph's current epistemic commitment; provenance is
the audit trail behind that commitment.

---

## Vocabulary

Use these terms consistently throughout the book. Do not treat them as synonyms.

| Term                  | Definition |
|-----------------------|------------|
| **Instance**          | A member of $V$ — the common root of both sorts. Realized as the Python class `Instance`, which carries the `id` field and the frozen model configuration. Every entity instance and every statement is an Instance; nothing else is. |
| **Entity type**       | A member of $T_\text{ent}$. Realized as a Python class inheriting from `EntityInstance`. Defines a class of entities: the fields they carry and their permitted roles in predicates. Example: `Person`, `Location`, `Moment`. |
| **Predicate type**    | A member of $T_\text{pred}$. Realized as a Python class inheriting from `BaseStatement`. Defines a class of propositions: their field schema, domain, range, traits, and truth status. Example: `LocatedIn`, `KnewAt`, `Treats`. |
| **Entity instance**   | A member of $V$ with $\tau(v) \in T_\text{ent}$. A concrete node — an instance of an `EntityInstance` subclass with an `id` and data fields. Example: a specific `Person` instance for Sherlock Holmes. A Statement is *not* an entity instance; both are Instances. |
| **Statement**         | A member of $V$ with $\tau(v) \in T_\text{pred}$. A concrete proposition — an instance of a `BaseStatement` subclass with an `id`, a `subject`, an `object_`, a `truth_status`, and metadata fields. Also a member of $E$ (the derived edge set). The term "edge instance" is an informal synonym when traversing the asserted graph. |
| **Field schema**      | $\Phi(t)$: the Pydantic model declaration of named fields and their types for type $t$. For predicate types, $\Phi$ includes `subject`, `object_`, and `truth_status` as distinguished fields whose type annotations constitute domain, range, and truth semantics. |
| **Domain**            | $\text{dom}(p)$ — the set of types permitted in the subject role for predicate $p$. Read from the type annotation of `subject` in $\Phi(p)$. |
| **Range**             | $\text{ran}(p)$ — the set of types permitted in the object role for predicate $p$. Read from the type annotation of `object_` in $\Phi(p)$. When $\text{ran}(p)$ includes a predicate type (i.e. a `BaseStatement` subclass), predicate $p$ enables higher-order claims. |
| **Trait**             | A declarative semantic property of a predicate type. Member of $\text{Tr}(p)$. Belongs to the schema, not to any instance. Realized as a Python mixin class. |
| **Schema**            | The tuple $(T,\ \Phi)$ together with trait declarations. Fixed at graph-design time. |
| **Instance graph**    | The tuple $(V,\ \tau)$. Populated at ingestion or reasoning time. |
| **Asserted graph**    | The subset of $E$ where `truth_status = asserted_true`, indexed for traversal. The first-order fact graph. |
| **Canonical identifier** | The value of $v.\text{id}$ for instance $v \in V$. Globally unique within $V$, assigned at construction, immutable, and opaque — it carries no type information. The Python realization is a `str` field (typically a UUID) on the `Instance` root class. Canonical IDs for real-world entities are sourced from authoritative ontologies (community-curated over long periods) rather than minted ad hoc; see the ontology authority declared in the domain section. |
| **Provenance**        | The set of fields $\Pi(p) \subseteq \Phi(p)$ that record how a predicate instance was produced: its source, extraction method, and any confidence scores. Provenance belongs to the instance (R2), is declared in the field schema (R10), and is distinct from truth status, which records the graph's current epistemic commitment to the proposition. |

### Terms to avoid or use carefully

- **Edge** — informal synonym for Statement when discussing traversal. Acceptable in casual prose; in definitions, use "Statement" or "predicate instance."
- **Relationship** — use this to mean a predicate instance, never a predicate type.
- **Node** — informal synonym for entity instance. Acceptable in casual prose, not in definitions.
- **Property** — overloaded; could mean a field on an instance or a trait on a predicate type. Be explicit.
- **Entity** — do not use as a synonym for "member of $V$." A Statement is a member of $V$ but is not an entity instance. When V-membership is what's meant, say "Instance."
- **Reification** — in this model, there is nothing to reify. Every predicate instance is already a member of $V$ and can be referenced by any predicate whose range includes its type. The word applies to models where edges and vertices are disjoint sorts; here they are not.

---

## Hard Rules

These rules follow from the formal definition and must not be violated in code
examples, schema designs, or explanatory prose.

**R1. Traits belong to predicate types, never to instances.** A predicate either
has `Transitive` or it does not. That is part of what the predicate *means*. An
individual instance cannot be transitive or non-transitive — that distinction
belongs to its type. In Python, traits are declared by inheriting the trait mixin
class alongside `BaseStatement`.

**R2. Metadata fields belong to instances, never to predicate types.** Provenance,
confidence, timestamps — all of these are facts about a particular assertion. They
live on the instance. The predicate type defines which fields are required (via
$\Phi$), but carries no values itself.

**R3. Every predicate instance is a directed, typed, truth-bearing proposition.**
A predicate instance carries `subject`, `object_`, `truth_status`, and whatever
additional fields $\Phi$ requires. It is simultaneously a proposition (it has
subject, predicate type, and object), a referable member of $V$ (it has an id
and can be referenced), and a potential edge in the asserted graph (if its
truth_status is `asserted_true`).

**R4. Domain and range are sets of types, not instances.** Formally,
$\text{dom}(p) \subseteq T$, not $\subseteq V$. You constrain which *kinds* of
things may appear as subject or object, not which specific things.

**R5. Schema is fixed; instances are populated.** Nothing discovered during
ingestion changes $T$, $\Phi$, domain, range, or traits. If a new type seems
necessary mid-ingestion, that is a schema design problem, not an ingestion problem.

**R6. Domain and range constraints are enforced by the Python type system.** Types
are Python classes. Each predicate class declares its `subject` and `object_` fields
with concrete class annotations. Mypy enforces these constraints statically; Pydantic
enforces them at construction time. When a predicate permits multiple subject or
object types, declare a `Union` (e.g. `subject: Person | Organization`). The field
name `object_` is used throughout to avoid shadowing the Python builtin `object`.

**R7. Pydantic models for instances are frozen.** Use
`model_config = ConfigDict(frozen=True)`. Instances are facts; they must not be
mutated after construction.

**R8. Higher-order predication is a schema-level type declaration, not a runtime
promotion.** A predicate enables higher-order claims when its range includes a
predicate type (a `BaseStatement` subclass). This is declared once in $\Phi$ at
schema design time — e.g. `object_: BaseStatement` on `KnewAt`. There is no runtime
"promotion" of instances between layers. Every predicate instance is a referable
member of $V$ from birth; whether any other predicate can point at it is determined
by the type declarations in $\Phi$.

Do **not** use higher-order predication to attach provenance, confidence, or
epistemic metadata to a proposition. That is R2's job — such fields belong on the
instance directly, declared in $\Phi$. Higher-order predication is for *predicating
over* a proposition (knowing it, disputing it, supporting it), not for *annotating*
one. Using it for annotation reintroduces exactly the overhead this model exists to
reject (see Non-Goals: *Not RDF/OWL*).

**R9. Every instance has a canonical, opaque, stable identifier.** The `id` field is
assigned at construction and does not change. It carries no type information — type
is the exclusive responsibility of $\tau$. For entity instances that correspond to
real-world referents, the id should be sourced from or aligned with an authoritative
ontology (community-curated over time, e.g. Wikidata QIDs, MeSH IDs, or a
domain-specific authority such as Baker Street Wiki). Minting ad-hoc IDs for named
entities that have established canonical IDs elsewhere is a traceability failure. Any
pattern that encodes entity type, predicate type, or relationship structure into an
identifier string and parses that string to recover it is a violation of both R6 and R9.

**R10. Every predicate type declares a provenance sub-schema.** $\Phi(p)$ must include
at minimum `source` and `extraction_method` for all $p \in T_\text{pred}$. These
fields are required, not optional — a predicate instance constructed without provenance
is a schema violation. Downstream consumers that cannot identify the origin of a claim
cannot audit, dispute, or weight it.

---

## Python Enforcement Pattern

The class hierarchy mirrors the formalism exactly. A single root class,
`Instance`, carries the `id` field and the frozen model configuration — it
realizes membership in $V$. Entity types are `EntityInstance` subclasses;
predicate types are `BaseStatement` subclasses with trait mixins inherited
alongside. `EntityInstance` and `BaseStatement` are disjoint siblings under
`Instance`: the sibling split realizes the strict partition of $T$, and
`BaseStatement` ⊂ `Instance` realizes $E \subseteq V$. A statement is
substitutable wherever "any member of $V$" is expected, and nowhere that
specifically an entity is expected — the type system asserts exactly what is
true and nothing more.

Domain and range constraints are expressed as Pydantic field type annotations —
no custom validation logic is needed. Traits are introspectable at runtime
(`issubclass(LocatedIn, Transitive)`), and `get_inverse` resolves declared
inverse pairs. The full schema is in `src/holmes_schema.py`.

---

## Non-Goals

**Not RDF / OWL.** In RDF, predicates are URIs and are themselves nodes; the graph
is a flat set of triples with no first-class edge objects. OWL adds description
logic semantics and open-world assumption. This model is a closed-world typed graph
where every predicate instance is a truth-bearing, field-carrying member of $V$. RDF
requires reification or named graphs for higher-order predication; this model
handles it through type declarations on domain and range.

**Not Neo4j's informal property graph.** Neo4j allows arbitrary key-value properties
on edges without schema enforcement. This model requires a declared field schema
($\Phi$) and enforced domain/range constraints. The structure is similar; the
discipline is different.

**Not an entity-relationship diagram.** ER diagrams are a database design tool.
This is a runtime knowledge representation with provenance, epistemic scope, and
trait-based inference semantics.

**Not a general ontology language.** This model does not support open-world reasoning,
class hierarchies, disjointness axioms, or the full OWL trait vocabulary. Traits are
a small, fixed set of declarative properties. If a use case seems to require full
description logic, that is scope creep.

**Not a stringly-typed system.** Types are Python classes, not ID prefixes. Domain
and range enforcement is the job of the Python type system and Pydantic, not of
string parsing. Any pattern that encodes type information inside an identifier
string and then parses that string to enforce constraints is a violation of R6.

---

## Current Domain: Holmes Corpus

The worked example uses the Sherlock Holmes canon as domain.

- **Ontology authority**: Baker Street Wiki (https://bakerstreet.fandom.com)
- **Schema construction method**: inductive — built by annotating stories, not pre-designed
- **Primary stories**: "A Scandal in Bohemia", "The Speckled Band"
- **Provisional entity types**: `Person`, `Location`, `Object`, `Document`,
  `Moment`, `Event`, `Persona`, `Plan`
- **Higher-order predicates**: `KnewAt`, `Contradicts` — these take `BaseStatement`
  in their range, enabling epistemic and dispute tracking
- **Epistemic fields on predicate instances**: `moment`, `narrator_confidence`,
  provenance fields
