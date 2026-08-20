# PMS-VECTOR — Dependency Map

## 1. Role and governing rule

This reference artifact externalizes the declared dependency and vulnerability topology of `PMS-VECTOR.md` Appendix F.9, synchronized with the option-status discrimination burden declared in Chapter 19.6. It is not an authority graph and does not generate warrant.

> **Governing rule:** dependency transmits vulnerability, not automatic warrant.

`dependency != warrant`  
`graph depth != theoretical importance`  
`node degree != epistemic strength`  
`visual adjacency != undeclared dependency`  
`option invisibility != option blindness`  
`warranted bounded negative != option blindness by default`  
`special-case or imagined probe != effective option`

Only relations explicitly present in `reference/Dependency Map.yaml` are graphable as dependency edges. Co-occurrence, shared source, or shared provenance status creates no edge.

## 2. Load-bearing roots

- `RobustOrientation`
- `OUGHT_account`
- `OUGHT_constraint`
- `TragicConstraint`
- `CorrigibleConsequenceCarryingPraxis`
- `StandaloneVECTOR`

## 3. RobustOrientation

```text
RobustOrientation
├── TypedDIR
├── RelevantROT
│   ├── DeclaredChange
│   ├── DeclaredConstants
│   ├── ReferenceContinuity
│   └── LossCapacity
├── SeriousRivalPressure
├── ProvenanceEchoAudit
├── NonCompensation
├── NoUnresolvedClaimCriticalRegression
└── PreservedResidue
```

### Failure / reduction routes

| Trigger | Response | Target(s) |
|---|---|---|
| materially relevant ROT is missing | `reduce_to` | BoundedOrientation |
| reference continuity fails | `reduce_or_retype` | NonCapture, Revision |
| serious rival defeats the baseline | `revise_or_collapse` | Revision, Collapse |

## 4. OUGHT_account

```text
OUGHT_account
├── RelevantOrientation
├── ExternalPremiseSupport
├── ActorUptake
├── EffectiveCapacity
├── ConsequenceRelation
└── RoleTimeConditions
```

### Failure / reduction routes

| Trigger | Response | Target(s) |
|---|---|---|
| relevant actor uptake is missing | `remove_orientation_mediated_uplift` | BaselineAttributability_LOGIC |
| effective capacity falls away | `reduce_or_retype` | OUGHT_account |
| external premise or consequence relation is defeated by a rival explanation | `weaken` | OUGHT_account |

## 5. OUGHT_constraint

```text
OUGHT_constraint
├── OperativeG
├── ExternalPremiseSupport
├── EffectiveOptionField
├── NonEmptyResponseClass
├── ResponsesSatisfyG
├── ConstraintActorRelationsVisible
├── ConstraintBurdenExitAlternativesVisible
├── TransformationRoutesConsidered
└── LegitimacyQuestionExternalized
```

`EffectiveOptionField` here means an evidence-typed field under the declared frame, role, time, capacity, dependency, and option-misrepresentation pressure. It does not imply complete or final access to the option field.

### Failure / reduction routes

| Trigger | Response | Target(s) | Boundary |
|---|---|---|---|
| a previously invisible effective option becomes evidenced within the relevant frame and time | `revise_response_class` | EffectiveOptionField | field revision does not by itself retroactively diagnose Option Blindness |
| G is revised | `new_constraint_claim_required` | OUGHT_constraint | — |
| system-level carriage is real but bearer assignment is unsupported | `collapse_person_assignment_preserve_system_relation` | SystemCarriageRequired, PersonXMustCarry | — |

## 6. TragicConstraint

```text
TragicConstraint
├── ValidOUGHTConstraint
│   └── OUGHT_constraint
│       ├── OperativeG
│       ├── ExternalPremiseSupport
│       ├── EffectiveOptionField
│       ├── NonEmptyResponseClass
│       ├── ResponsesSatisfyG
│       ├── ConstraintActorRelationsVisible
│       ├── ConstraintBurdenExitAlternativesVisible
│       ├── TransformationRoutesConsidered
│       └── LegitimacyQuestionExternalized
├── EffectiveOptionField
├── ResidualLossAcrossRG
├── SecondOrderMechanismAudit
├── CapacityAuthorityDependencyWindowAudit
└── NoEvidencedNonLossfulRoute
```

`SecondOrderMechanismAudit` requires evidenced mechanism discrimination between realistic transformability and merely imagined possibility. Blindness pressure does not require an analyst to invent an alternative where no effective route is evidenced.

### Failure / reduction routes

| Trigger | Response | Target(s) | Boundary |
|---|---|---|---|
| a previously invisible effective no-loss option is evidenced | `collapse` | TragicConstraint | current tragic classification collapses; prior invisibility alone does not establish prior Option Blindness |
| Second-Order Agency can create or open a non-lossful route in time | `collapse` | TragicConstraint | transformation must be mechanism-supported, not merely conceivable |
| a rival demonstrates that the option/constraint field was materially misdescribed, including unsupported exclusion or unsupported inclusion where material | `collapse_or_retype` | TragicConstraint | misdescription can contract or inflate the represented field |
| only an imagined rescue route exists | `no_current_option_status_change` | ImaginedRescueRoute | imagined or special-case rescue may remain a probe; no effective option status follows unless its constitutive conditions are evidenced |

## 7. CorrigibleConsequenceCarryingPraxis

```text
CorrigibleConsequenceCarryingPraxis
├── BoundedRelevantOrientation
├── PreservedCorrigibility
├── CarriesRelevantConsequence
├── CapacityLimitsAwareness
├── PreservedResidue
├── OrientationEnactmentSourceConditions
├── DistributedAgencyRecognition
└── NoOrientationAuthorityConversion
```


## 8. StandaloneVECTOR

```text
StandaloneVECTOR
├── DIRAddsDiscrimination
├── ROTAddsBeyondReframing
├── DISTCoreReductionAccepted
├── AdultOrientationReductionAccepted
├── OptionStatusDiscrimination
│   ├── InvisibilityBlindnessSeparation
│   ├── BlindnessBoundedNegativeSeparation
│   ├── ProbeIllusionSeparation
│   ├── BidirectionalFieldCorrection
│   └── OptionTaxonomyReducibility
├── SecondOrderAgencyMechanismDiscrimination
├── CostTopologyDiscrimination
├── ConvergenceClosureClaimLossAndStop
├── SpecialistRivalCanWin
├── SameBurdenRivalCanReduce
├── OughtTypologyPreventsConflation
└── CoreRemainsReducible
```

`OptionStatusDiscrimination` is corrigibly bidirectional: evidence may expand or contract the represented option field. Its validity depends on keeping Invisibility distinct from Blindness, bounded negatives distinct from Blindness, probes distinct from Illusion, and the taxonomy itself reducible if those distinctions fail or become post hoc stabilization vocabulary.

### Failure / reduction routes

| Trigger | Response | Target(s) |
|---|---|---|
| every major component becomes losslessly reducible to pre-existing PMS terms | `reduce_to` | Reduction |
| one component fails its independent discrimination burden | `component_reduction` | Reduction |
| VECTOR works only in a narrower domain | `scope_reduction_or_domain_non_capture` | Reduction, NonCapture |
| same-burden rival provides same or better discrimination with less complexity | `stronger_scope_reduction` | Reduction |

## 9. Prohibited inferences

These are explicit blocked jumps. They may be rendered by a Reader as negative or blocked edges, but they are not ordinary dependencies.

| Source | Relation | Target | Reason |
|---|---|---|---|
| `RelevantOrientation` | `does_not_imply` | `DecisionAuthority` | orientation does not increase authority by itself |
| `PresentedOrientation` | `does_not_imply` | `ActorUptake` | being told is not uptake |
| `SystemCarriageRequired` | `does_not_imply` | `PersonXMustCarry` | system-level carriage does not specify a person-level bearer |
| `FixpointRequirement` | `does_not_imply` | `BearerAuthority` | functional necessity does not create bearer authority or legitimacy |
| `SourceG` | `does_not_imply` | `LegitimacyG` | source or maintenance of G does not establish legitimacy of G |
| `ExternalPremiseSupport` | `does_not_warrant_by_dependency_alone` | `OUGHT_account` | a dependency relation transmits vulnerability, not automatic warrant |
| `ActorUptake` | `not_required_for_baseline` | `BaselineAttributability_LOGIC` | VECTOR uptake gates orientation-mediated uplift only; it does not rewrite LOGIC baseline attributability |
| `DIST` | `not_equivalent_to` | `REV_avail` | derived orientation–enactment separation is not effective reversibility availability |

## 10. Enacted reductions

These are reductions already carried by the current PMS-VECTOR architecture. They are not hypothetical failure routes.

| Target | Previous status | Current status | Pressure |
|---|---|---|---|
| `DIST` | `core_component` | `derived_supporting_only` | E22; Appendix E.22 / Chapter 19.5 |
| `AdultOrientation` | `independent_construct_candidate` | `supporting_shorthand_only` | Chapter 19.12 deletion / discrimination test |
| `DIR` | `core` | `retain_scope_narrowed` | E23; Appendix E.23 same-burden rival superiority |
| `ROT` | `core` | `retain_scope_narrowed` | E23; Appendix E.23 same-burden rival superiority |
| `StandaloneVECTOR` | `standalone_theory_candidate` | `provisional_retain_scope_reduced` | E23; Appendix E.23 same-burden rival superiority |

### E.21 / E.23 boundary

- **E.21** is specialist-domain deference. It does not by itself narrow DIR–ROT on VECTOR's own bounded orientation burden.
- **E.23** is same-burden rival superiority and is the enacted pressure behind the present DIR, ROT, and Standalone VECTOR scope narrowing.

### Adult Orientation boundary

`AdultOrientation` is not a load-bearing dependency root. Chapter 19 shows that the dependency structure remains intact when the label is deleted; the substantive root is `CorrigibleConsequenceCarryingPraxis`.

## Closing dependency contract

- Dependency tells us what becomes vulnerable if a required relation fails.
- Dependency does not tell us that the dependent claim is warranted.
- Failure routes specify reduction, revision, collapse, or Non-Capture where declared; they do not force binary rejection.
- Option-status discrimination is bidirectional and remains vulnerable to reduction if its distinctions do not discriminate.
- Later discovery of an effective option revises the current field where warranted; it does not by itself establish earlier Option Blindness.
- Provenance categories are maintained separately in `reference/Claim Provenance.yaml`.
- Reader graphs may visualize only declared dependency, prohibited-inference, case-pressure, provenance-routing, or reduction relations from their owning artifacts.
