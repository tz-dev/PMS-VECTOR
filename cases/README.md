# PMS-VECTOR Appendix-E Case Suite

The `cases/` directory contains the **23 paper-linked Appendix-E fixtures** used by PMS-VECTOR as an internal conformance and adversarial pressure layer. The suite supports inspectability, counterexample pressure, and architecture reduction where warranted; it is **not empirical validation** and does not acquire theoretical authority from case count or implementation detail.

```text
case count != evidential weight
case suite != empirical validation
case outcome != VECTOR validity
CaseFixture != TheoryAuthority
```

## File roles

Each paper-linked case is represented as a pair:

```text
E##.yaml   structured case record
E##.md     human-readable case companion
```

Supporting files have different roles:

| Path | Role |
| --- | --- |
| `cases/index.yaml` | Inventory and Reader navigation registry |
| `model/Case.template.yaml` | Case scaffold with embedded VECTOR Record envelope |
| `model/VECTOR-Record.template.yaml` | Generic bounded VECTOR Record scaffold |
| `PMS-VECTOR.md` Appendix E | Paper-linked case source and theoretical authority |

The index and templates support navigation and recording. They do not independently establish a case result or VECTOR construct.

## Case formats

The suite uses two paper-linked fixture formats:

- **`full_minimal_case`** — develops enough structure to show why the output changes.
- **`micro_countercase`** — isolates a tempting misclassification and the correction VECTOR must make.

These format labels describe fixture shape, not evidential weight.

## Adversarial and load-bearing subsets

The paper-declared adversarial subset is:

```text
E01, E04, E11, E21, E22, E23
```

`E04` is explicitly included in that subset although its paper Type line carries no lettered adversarial label; the repository does not infer a missing label.

Four cases are especially load-bearing in the current architecture:

| Case | Function |
| --- | --- |
| `E20` | Non-Capture |
| `E21` | Specialist rival superiority / domain-task deference |
| `E22` | DIST necessity test and architecture reduction |
| `E23` | Same-burden rival superiority and DIR/ROT/standalone scope reduction |

A collapse, rival victory, suspension, reduction, or Non-Capture can be the correct discriminative result.

## Pressure-family and construct labels

Pressure-family labels in `index.yaml` are **navigational classifications**, not a new theoretical taxonomy. Likewise, `constructs_under_test` records what a fixture pressures; it does not pre-validate the construct or prescribe the case result.

```text
PressureFamilyLabel != VECTORConstruct
IndexClassification != CaseResult
```

## Template and case synchronization

The templates are scaffolds, **not strict validation schemas**. Conditional blocks may remain unused when they are not material. Template evolution does not automatically rewrite, validate, or reclassify existing paper-linked cases.

```text
Template != strict validation schema
TemplateEvolution != CaseReclassification
```

A new field or taxonomy should be assigned to an existing case only after substantive case-level review. This is especially important for the current option architecture: **Option Invisibility is a legibility condition, not by itself an option-field error; Option Blindness and Option Illusion are opposite forms of Option-Field Misrepresentation.** A case receives such a diagnosis only where its own actor-, role-, time-, frame-, capacity-, dependency-, and evidence-bound record supports it.

Accordingly, the current template synchronization does not by itself convert `E07 — Option Invisibility` into an Option Blindness case, does not add an Option Blindness fixture, and does not otherwise reclassify E01–E23.

## Authority and use

When a case artifact conflicts with the paper or the canonical model layer, the conflict should be exposed and reviewed rather than silently reconciled. Case artifacts are pressure instruments and inspectable records; they are not verdict machinery.

For the full inventory, see [`index.yaml`](index.yaml). For VECTOR theory, see [`../PMS-VECTOR.md`](../PMS-VECTOR.md).
