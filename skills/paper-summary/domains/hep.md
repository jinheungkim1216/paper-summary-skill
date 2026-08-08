# Domain supplement — high energy physics (HEP)

Write a **도메인 보충: HEP (<subcategory>)** section. First cover the **common**
items, then **branch on `manifest.domain_subcategory`** (hep-ex / hep-ph /
hep-th / hep-lat) and use only that block. Quote every number and uncertainty
verbatim with a reference tag (e.g. `(§5, Table 2)`); keep σ/CL/units exact.

## Common (all hep-*)
- **Physics motivation** — which Standard Model (SM) test or beyond-SM (BSM)
  question this addresses; the process/observable at stake.
- **Theoretical/experimental context** — what prior results or theory it builds
  on or challenges.

## If hep-ex (experiment)
- **Collider & dataset** — machine, √s, integrated luminosity (fb⁻¹), run period.
- **Detector & objects** — relevant subdetectors, reconstructed objects (jets,
  leptons, MET, b-tagging).
- **Analysis** — signal region(s), event selection, dominant **backgrounds** and
  how they're estimated (data-driven vs. MC).
- **Systematic uncertainties** — the leading ones and their size (table).
- **Statistics & result** — observed vs. expected, significance (in σ) for a
  discovery/evidence, or the **limit** set (e.g. 95% CL exclusion), measured
  values with uncertainties.

## If hep-ph (phenomenology)
- **Model / framework** — the BSM scenario or EFT; free parameters and benchmark
  points.
- **Calculation** — cross sections, decay rates, branching ratios; order in
  perturbation theory; tools used.
- **Predicted observables** — signatures and where they'd show up.
- **Constraints** — bounds from existing data (collider, flavor, cosmology) on
  the parameter space.
- **Experimental prospects** — testability at current/future experiments.

## If hep-th (theory)
- **Formal structure** — symmetries, dualities, algebraic/geometric setup.
- **Central results** — theorems, derivations, what is proven vs. conjectured.
- **Assumptions & regime of validity** — limits, backgrounds, approximations.
- **Consistency checks & implications** — known-limit recovery, connections to
  other results.

## If hep-lat (lattice)
- **Lattice setup** — action, lattice spacings, volumes, quark masses, ensembles.
- **Systematics** — continuum limit, finite-volume, discretization,
  renormalization; how each is controlled.
- **Results** — physical quantities with statistical + systematic errors;
  comparison to experiment / other lattice groups.
