# European Business Faculty Search Engine — Build Plan

A searchable, continuously refreshed directory of every academic staff member in
every business/management/economics faculty in Europe, with each person's research
specialties normalized to a controlled taxonomy and **independently corroborated by
at least two sources** before publication.

> **Status:** plan only. No code written yet. This document is the spec to build against.
>
> **Repo note:** this is a separate product from the TriAgent Instagram agent that
> currently occupies this repository. The plan lives here for review; the
> implementation should get its own repo (`eubfs` / `faculty-index`) before Phase 2.

---

## 1. Scope and target numbers

| Quantity | Working estimate | Firmed up in |
|---|---|---|
| European HEIs (all types) | ~3,300 (ETER register) | Phase 1 |
| HEIs with a business/management/economics faculty | 1,000–1,500 | Phase 1 |
| Academic staff in scope | 80,000–150,000 | Phase 2 pilot extrapolation |
| Countries | 44 (Council of Europe footprint), 24+ languages | Phase 1 |

These are **estimates**, deliberately labelled as such. Phase 1 replaces every row
with a counted number; no downstream capacity or cost planning should treat them as
facts until then.

**"Europe"** = geographic Europe, not EU-27. Includes UK, Switzerland, Norway,
Western Balkans, Turkey, Ukraine. Countries are attached from ROR/ETER country codes,
so the boundary is a config list, not a hard-coded assumption.

**"Business faculty"** = the organizational unit granting degrees in business
administration, management, accounting, finance, marketing, or economics-in-a-business-school.
Standalone economics faculties are **in scope** (the business/econ boundary is not
consistent across Europe and excluding them loses a large share of finance and
strategy researchers). Law, psychology, and industrial-engineering faculties are out
of scope even when they teach management courses.

**"Member"** = academic staff with a research or teaching appointment: professors
(full/associate/assistant), lecturers/readers, postdocs, and PhD candidates where the
faculty lists them publicly. Administrative staff are excluded. Emeriti are included
but flagged, because they distort "current headcount" queries.

**Per-person target schema:**

```
person_id, full_name, given_name, family_name, name_variants[],
institution_ror, faculty_unit, country, title/rank, is_emeritus,
profile_url, orcid, openalex_author_id, repec_id,
specialties[] {taxonomy_node, label, confidence, evidence_source[]},
free_text_interests, languages_detected,
first_seen, last_verified, confidence_tier, sources[]
```

Deliberately **not** collected: email addresses, phone numbers, photographs, personal
addresses, dates of birth, or anything else not required to answer "who works on what,
where." See §7 — data minimization is a legal requirement here, not a preference.

---

## 2. Architecture

Six stages, each independently restartable and independently testable. Every stage
writes to durable storage; nothing is held only in memory across stages.

```
┌─────────────────────┐
│ 1. Institution      │  ROR + ETER + accreditation lists + EDIRC + Wikidata
│    registry         │  → institutions table (ROR id, country, domain, faculty URL)
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ 2. Directory        │  sitemap parse + URL probing + multilingual patterns
│    discovery        │  → staff_directory_urls (per institution, with method + score)
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ 3. Fetch + extract  │  CRIS APIs first, then structured data, then LLM fallback
│                     │  → raw_person_records (with source span for every field)
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ 4. Entity           │  ORCID + OpenAlex + RePEc + Crossref
│    resolution +     │  → persons (canonical) with confidence_tier
│    cross-validation │
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ 5. Specialty        │  taxonomy mapping, evidence-weighted
│    normalization    │  → person_specialties
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ 6. Search service   │  hybrid BM25 + dense retrieval + structured filters
└─────────────────────┘
```

---

## 3. Stage 1 — Institution registry

Do **not** start by crawling. Start by knowing exactly which institutions exist. A
counted denominator is what makes recall measurable later; without it, "we found
40,000 people" is an unfalsifiable number.

Sources, merged on ROR ID:

- **ROR** (Research Organization Registry) — CC0, full dump + API, gives canonical ID,
  country, official website, name variants and acronyms. This is the join key for
  everything downstream.
- **ETER** (European Tertiary Education Register) — the official EU HEI register,
  downloadable, includes student/staff counts and field-of-study breakdowns. Best
  single source for "does this institution teach business."
- **RePEc / EDIRC** — a country-by-country directory of economics and business
  departments worldwide, already at *department* granularity rather than institution
  granularity. Unusually well-matched to this project; it short-cuts a lot of Stage 2.
- **Accreditation registers** — EFMD (EQUIS/EPAS/EOCCS), AACSB, AMBA. These identify
  the business-school entity by name and confirm it is a business school. Coverage is
  partial (accredited schools skew large and rich), so they are a precision signal,
  not a recall source.
- **Wikidata** SPARQL — links ROR/GRID/ISNI, official websites, and parent/child
  organizational relations, which helps attach faculties to their parent university.

Output table `institutions`: `ror_id, name, name_variants[], country, official_domain,
has_business_faculty (bool + evidence), business_faculty_name, business_faculty_url,
cris_system (pure|converis|vivo|dspace|none|unknown), source_flags[]`.

**Deliverable:** a counted institution list with a per-country breakdown, reviewed by
hand for at least 5 countries before Stage 2 begins. Expect to find that ETER and ROR
disagree on institution boundaries (mergers, federated universities like the French
COMUEs, the UK collegiate system) — resolve these manually, they are a long tail of
a few dozen, not a modelling problem.

---

## 4. Stage 2 — Directory discovery

For each institution, find the URL(s) listing business-faculty staff. This is the
stage most likely to be underestimated: it is where the multilingual,
inconsistent-CMS reality of 1,200 university websites lands.

Approach, in order of preference:

1. **CRIS detection first.** A large share of European universities run a research
   information system with a public, structured, paginated API:
   - **Elsevier Pure** — `https://research.<domain>/ws/api/persons`, consistent JSON
     schema with person, organizational affiliation, and *stated research keywords*.
     Very common in the Nordics, NL, UK, IE.
   - **Converis**, **VIVO**, **DSpace-CRIS**, **Symplectic Elements**.
   Detecting a CRIS turns extraction from "parse arbitrary HTML" into "call an API,"
   and it is usually the highest-quality specialty source available. **Probe for this
   before writing a single HTML selector.**
2. **`sitemap.xml` / `robots.txt` sitemap directives** — parse and filter for
   person-page URL patterns.
3. **Multilingual URL and link-text probing** against the faculty domain:
   - en: `people`, `staff`, `faculty`, `academic-staff`, `our-team`, `members`
   - de: `mitarbeiter`, `personen`, `team`, `lehrstuhl`, `professuren`
   - fr: `equipe`, `enseignants-chercheurs`, `annuaire`, `membres`, `corps-professoral`
   - es/ca: `profesorado`, `personal`, `directorio`, `professorat`
   - it: `docenti`, `personale`, `rubrica`
   - nl: `medewerkers`, `wie-is-wie`, `team`
   - pt: `docentes`, `corpo-docente`
   - pl: `pracownicy`, `kadra`, `zespol`
   - sv/no/da: `medarbetare`, `ansatte`, `personale`
   - fi: `henkilosto`, `henkilokunta`
   - cs/sk: `zamestnanci`, `lide`, `katedra`
   - el: `prosopiko`, `didaktiko-prosopiko`
   - tr: `akademik-kadro`, `personel`
   - uk/ru: `spivrobitnyky`, `sotrudniki`, `kafedra`
   Maintain this as a data file, not code, so it can be extended per country without
   a deploy. Include diacritic-stripped variants.
4. **Site-internal search** where a search endpoint exists.
5. **Manual entry** — a reviewed CSV for institutions the automation fails on. Budget
   for 10–15% of institutions needing this. It is cheaper than trying to automate the
   last mile, and it is the difference between 85% and 98% institutional coverage.

Each discovered URL is stored with the method that found it and a confidence score,
so failures are diagnosable per-institution rather than as an aggregate.

---

## 5. Stage 3 — Fetch and extract

### Fetching

- Respect `robots.txt` (including `Crawl-delay`), and treat a disallow as final.
- Identifying `User-Agent` with a project URL and a contact email address. Anyone
  who wants to be excluded must be able to find out who to ask.
- Per-domain rate limit: 1 request / 2s default, backing off on 429/503. Politeness
  is not optional at this scale — a university IT department that notices you is a
  blocked domain and a lost institution.
- Conditional requests (`ETag`/`If-Modified-Since`) and content hashing so recrawls
  cost near-nothing when nothing changed.
- Cache every raw response (compressed, content-addressed). Re-extraction must never
  require re-fetching — extraction logic will change many times, the pages will not.
- Headless browser (Playwright) only for the subset of pages that genuinely require
  JS. Detect by comparing static HTML text length against rendered length on a sample;
  route per-domain, not globally. Browser rendering is ~50× the cost of a plain fetch.

### Extraction — deterministic first, LLM as fallback

The rule: **never** send a page to an LLM if a parser can read it reliably. This is a
cost decision and an accuracy decision at the same time.

1. **CRIS API responses** → direct field mapping. Zero ambiguity.
2. **Structured markup** — schema.org `Person` JSON-LD, microdata, `hcard`. More
   common than expected on modern university CMSs.
3. **Known CMS templates** — TYPO3, Drupal, WordPress staff-directory plugins produce
   identical DOM structures across hundreds of institutions. Write a template
   fingerprint + selector set once, reuse across every site sharing it. A few dozen
   templates plausibly cover a large fraction of remaining pages; measure this in the
   pilot before committing to how many to write.
4. **LLM extraction** for the genuinely unstructured remainder, using Claude with a
   strict tool/output schema:
   - Model routing: **Haiku 4.5** for the bulk of list-page and profile-page
     extraction; escalate to **Opus 5** only for pages the cheap path flags as
     ambiguous (low field-fill rate, conflicting names, unparseable layout).
   - **Every extracted field must carry the verbatim source substring it came from.**
     Reject any field whose claimed source span is not literally present in the input.
     This is the single most effective anti-hallucination control available and it is
     a cheap post-hoc string check, not a model behavior you have to trust.
   - Chunk long list pages with overlap; deduplicate across chunks by name.
   - Never let the model infer a specialty that is not written on the page. Absent is
     a valid answer and must be an explicit enum value in the schema, so the model has
     somewhere to put "not stated" other than a guess.

### Specialty raw signals

Collect, unnormalized, from: stated research interests, chair/professorship title
(highly informative in German-speaking countries — "Lehrstuhl für Marketing" *is* the
specialty), department/subject-group membership, taught modules, and self-described
bio text.

---

## 6. Stage 4 — Entity resolution and cross-validation

This is the stage that determines whether the dataset is worth anything. The
requirement is explicit: **accurate, and validated against an independent source.**

### Resolution

Blocking on normalized family name + country, then scoring candidate pairs on given
name/initials, institution, ORCID, co-author overlap, and specialty similarity.
Handle transliteration (Greek, Cyrillic), diacritics, particles (van/de/von), Hungarian
name order, patronymics, and double-barrelled Spanish and Portuguese surnames. A name
normalization library plus per-country rules; do not try to solve this generically.

Merge only above a high threshold. Below it, hold as `possible_duplicate` for review —
a wrongly merged pair of two real people is a worse failure than a duplicate, because
it silently attributes one person's work to another.

### Validation sources

| Source | Licence / access | Validates | Strength |
|---|---|---|---|
| **ORCID** public API | free, CC0 public record | affiliation (person-asserted), name | ★★★ |
| **OpenAlex** | free, CC0, bulk + API | affiliation (from publications), **specialty via topics** | ★★★ |
| **RePEc / IDEAS author service** | free | affiliation + field, business/econ-specific | ★★★ |
| **Crossref** | free | publication record, affiliation strings | ★★ |
| **Wikidata** | CC0 | senior figures only, sparse | ★ |
| **Scopus / Web of Science** | licensed | affiliation, subject areas | ★★★ if budget allows |
| **LinkedIn** | see §7 | employment | not used automatically |

**OpenAlex is the workhorse for specialty validation.** It disambiguates authors and
assigns topics from actual publication output — an empirical, independent read on what
someone works on, rather than what a departmental webpage says they work on. Where the
webpage says "strategy" and OpenAlex says operations management, that disagreement is
itself valuable signal and should surface for review rather than being silently resolved.

**RePEc deserves specific attention** for this project: its registered-author service
covers economics and business researchers at exactly the granularity needed, is
self-asserted (so affiliations are current), and is European-heavy in coverage.

### Confidence tiers

Nothing is published without corroboration. A record's tier is:

- **Verified** — name + institutional affiliation confirmed by ≥2 independent
  sources, at least one non-institutional (i.e. not just the university's own page),
  **and** specialty supported by ≥2 sources agreeing at the coarse taxonomy level.
- **Corroborated** — affiliation confirmed by ≥2 sources; specialty from one source only.
- **Single-source** — institutional page only, no external match. Retained but
  **excluded from default search results** and clearly labelled. This tier will be
  large for PhD students and junior staff with no publication record yet — that is
  expected and correct, not a bug to engineer away.
- **Conflicted** — sources disagree on affiliation or coarse specialty. Queued for
  human review; never silently auto-resolved.

Publish the tier alongside every record in the API. A search engine that hides its own
uncertainty is worse than one that exposes it.

---

## 7. Legal and ethical constraints — read before Phase 1

This is a database of personal data about identifiable people, most of them EU or UK
residents. It is squarely within GDPR/UK-GDPR scope. Publicly available ≠ free to
process. These are not optional workstreams.

**Required before any bulk collection:**

1. **Legitimate Interests Assessment** (Art. 6(1)(f)) — documented purpose, necessity,
   and balancing test. Academic staff have a reasonable expectation that their
   professional research profile is discoverable; this is a defensible basis, and the
   assessment must be written down before collection, not after a complaint.
2. **DPIA** — large-scale processing of personal data, so likely required under
   Art. 35. Do it.
3. **Art. 14 transparency** — data collected indirectly triggers a notification duty.
   The `14(5)(b)` disproportionate-effort exemption is arguable at this scale, but it
   is conditional on publishing a clear, findable privacy notice. Publish one, at a
   stable URL, linked from every page of the product.
4. **Data minimization** — the schema in §1 excludes contact details deliberately.
   Do not "collect it since it's there." Collecting emails converts this from a
   research-discovery tool into a marketing list, which changes the legal analysis
   entirely and invites the complaints that follow.
5. **Rights workflow** — a working route for access, rectification, objection, and
   erasure, with a documented SLA and a suppression list that survives recrawls. A
   person who asks to be removed must not reappear at the next crawl. This is the
   detail that most scraped datasets get wrong.
6. **No special-category data** (Art. 9), no inference of gender, ethnicity, or
   nationality from names. If a downstream user wants diversity analytics, that is a
   separate product with a separate legal basis.

**On LinkedIn specifically.** The request named LinkedIn as the validation source.
Recommendation is to **not** use it as an automated one:

- LinkedIn's User Agreement prohibits automated scraping, and they enforce it actively
  (technical countermeasures and litigation). *hiQ v. LinkedIn* held that scraping
  public profiles is not a CFAA violation, but that is US computer-crime law — it does
  not cure a breach-of-contract claim, and it has no bearing on GDPR.
- LinkedIn's official APIs do not offer people-search to general developers; there is
  no compliant automated path to the data at this scale.
- Substantively, ORCID + OpenAlex + RePEc are *better* validators for this specific
  question. They are machine-readable, licensed for reuse, and carry publication
  evidence for the specialty claim, which LinkedIn does not.

Where LinkedIn is genuinely useful is **human-in-the-loop QA**: a reviewer manually
checking a sampled record against a public profile, at human scale, is ordinary
professional research and raises none of the above problems. That is how it appears in
this plan — in §9's golden-set review, not in the pipeline.

If automated LinkedIn coverage is a hard product requirement, the honest options are a
commercial licensed data provider (Proxycurl-style vendors, or LinkedIn's own Talent
Solutions) with the cost and contract that implies. That is a procurement decision,
not an engineering one, and it should be made explicitly rather than by quietly
pointing a scraper at linkedin.com.

---

## 8. Stage 5 — Specialty normalization

Free-text research interests are unsearchable across languages: "Marketing",
"Absatzwirtschaft", "Mercadotecnia" and "consumer behaviour" must land in comparable
places. Store the raw text *and* a normalized mapping.

**Taxonomy:** two levels.

- **Level 1 (12–15 divisions):** Accounting; Finance; Marketing; Management & OB;
  Strategy; Operations & Supply Chain; Information Systems; Entrepreneurship &
  Innovation; HRM & Employment Relations; International Business; Business Ethics,
  CSR & Sustainability; Economics; Business Law & Governance; Quantitative Methods.
- **Level 2:** ~150–250 subtopics, seeded from **JEL codes** (the standing
  classification in economics and finance), the **AJG/ABS journal field list**, and
  **OpenAlex topics**, then pruned to what actually occurs in the data.

Crosswalk tables from JEL → L2 and OpenAlex topic → L2 are maintained as reviewed data
files. Build them from the pilot's observed distribution rather than designing the full
taxonomy up front — a taxonomy designed in advance will have branches nothing maps to
and gaps where the data is dense.

**Assignment** combines: LLM classification of the free-text interests (with the
taxonomy in the prompt and abstention allowed), chair/professorship title mapping, and
the empirical distribution of the person's OpenAlex topics. Where these agree,
confidence is high; where they disagree, keep both with sources attached. A person can
legitimately hold several specialties — model it as a weighted set, never a single label.

---

## 9. Quality measurement

Without this, none of the accuracy claims above mean anything.

**Golden set:** ~500 hand-labelled faculty across ~25 institutions, deliberately
stratified — 10+ languages, CRIS and non-CRIS sites, large and small schools, at least
5 institutions with deliberately awkward sites. Built by hand, by a person, from the
institutional pages and cross-checked against ORCID/OpenAlex (and LinkedIn manually,
per §7). This is a multi-day task and it is the highest-leverage work in the project.

**Metrics, reported per country and per stage:**

- *Roster recall* — of the people genuinely on a faculty page, how many did we find?
- *Roster precision* — of the people we listed, how many are actually in-scope academic staff?
- *Field accuracy* — name, rank, affiliation correctness.
- *Specialty accuracy* — L1 exact-match and L2 top-1/top-3 against the labels.
- *Validation rate* — share of records reaching Verified/Corroborated tiers.

**Suggested initial gates for a public launch** (to be revisited after the pilot gives
real baselines rather than treated as fixed): roster recall ≥0.95 and precision ≥0.98
on the golden set, L1 specialty accuracy ≥0.90, ≥80% of records at Corroborated or
better. Precision is weighted above recall throughout: a missing person is a gap, a
wrong person is a false claim about someone.

**Ongoing:** re-crawl quarterly, with an intensive pass each September–October when
European academic appointments turn over. Alert on distribution drift (a school whose
headcount drops 40% overnight is a broken selector, not a mass resignation).

---

## 10. Stage 6 — The search service

- **Store:** PostgreSQL as the system of record; OpenSearch (or `pgvector`, if the
  simpler deployment matters more than ranking quality) for retrieval.
- **Retrieval:** hybrid — BM25 over name/title/interests, dense embeddings over the
  specialty and bio text for cross-lingual matching, fused with reciprocal-rank fusion.
  Cross-lingual embeddings are what let a query for "supply chain resilience" return a
  German-language profile that never uses those words.
- **Filters:** country, institution, faculty, L1/L2 taxonomy node, rank, emeritus flag,
  confidence tier, last-verified date.
- **API:** FastAPI — `/search`, `/person/{id}`, `/institution/{ror}`, `/taxonomy`.
  Every response carries provenance: sources, confidence tier, and `last_verified`.
- **UI:** thin. Search box, filter rail, result cards showing name / institution /
  specialties / confidence, and a person page that shows the evidence trail. The
  evidence trail is the product's credibility — do not bury it behind a disclosure.
- **Public-facing:** privacy notice, an obvious "request correction or removal" link on
  every person page, and rate limiting to prevent trivial bulk re-scraping of the
  result of all this work.

---

## 11. Phasing

| Phase | Work | Exit criterion |
|---|---|---|
| **0. Legal groundwork** | LIA, DPIA, privacy notice, rights workflow, retention policy | Signed off before any bulk fetching |
| **1. Institution registry** | ROR/ETER/EDIRC/accreditation merge; manual review | Counted, reviewed institution list with business-faculty flags |
| **2. Pilot — 20 institutions** | End-to-end on a deliberately hard, multilingual sample; golden set built | Honest per-stage metrics; extrapolated scale and cost |
| **3. Extraction at scale** | CRIS connectors, CMS templates, LLM fallback, caching, politeness | ≥90% of in-scope institutions with a parsed roster |
| **4. Validation + taxonomy** | ORCID/OpenAlex/RePEc matching, tiering, specialty normalization | Metric gates in §9 met on the golden set |
| **5. Search service** | Index, API, UI, provenance display, removal workflow | Internal launch |
| **6. Full Europe + refresh** | Remaining countries, quarterly refresh, drift monitoring | Public launch |

Phase 2 is the decision point. It exists to produce real numbers for coverage, cost,
and accuracy on a hard sample, and its output should be allowed to change the plan —
including changing the scope. Extrapolating from an easy pilot is the standard way
projects of this shape end up 6 months late.

---

## 12. Cost and effort — order of magnitude

**LLM extraction** is the main variable cost, and it is smaller than it first looks
because the deterministic paths carry most of the volume:

- Assume ~150k pages needing LLM extraction after CRIS/structured/template paths take
  their share, at ~4k input tokens each ≈ **0.6B input tokens**.
- Routed predominantly to Haiku 4.5, with a minority escalated to Opus 5, this is a
  **low-thousands-of-dollars** one-off, and materially less per quarterly refresh once
  content hashing means only changed pages are re-extracted.
- The dominant lever is the deterministic-vs-LLM split. Every CMS template written is
  a permanent reduction in recurring cost. Measure that split in Phase 2 before
  sizing anything.

**Other costs:** modest compute and storage (the raw page cache is the largest object,
and it compresses well); optional Scopus/WoS licensing; and the real one — **human
review time** for the golden set, conflicted records, and the manual-entry institution
tail. Budget that explicitly rather than hoping automation absorbs it. It will not.

**Rough team shape:** one engineer can reach Phase 2 alone. Phases 3–4 want two, plus
part-time reviewer capacity and access to someone who can sign off on §7.

---

## 13. Principal risks

| Risk | Mitigation |
|---|---|
| Directory discovery underperforms on the long tail of small/non-English sites | Manual CSV path budgeted from the start (§4); measured per country, not in aggregate |
| LLM hallucinates people or specialties | Mandatory verbatim source spans, verified by string containment; abstention as an explicit schema value; precision-weighted gates |
| Legal challenge or takedown demand | §7 done before collection, not after; working removal workflow; no contact data collected |
| Blocked by university IT | Politeness limits, identifying UA with contact address, honour robots.txt, back off on the first sign of trouble |
| Entity resolution merges two distinct people | High merge threshold; `possible_duplicate` review queue; never auto-merge on name+country alone |
| Data staleness at academic-year turnover | Quarterly refresh with an intensive Sept–Oct pass; `last_verified` exposed on every record |
| Scope creep from "business" into all of social science | Scope rule in §1 enforced at the institution registry, where it is one reviewed decision per institution instead of thousands per person |
