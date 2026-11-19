# Phase 1 acceptance check: integrity store

Store built 2026-11-18T11:57:44+00:00 from 73877 Retraction Watch notices (sha256 `f1817ae2e4f8`). Sample size 50, seed 7. Probes 1 and 2 are deterministic for a given store and seed; probes 3, 3b and 4 query live APIs and vary between runs.

## 1. JMIR 2026 study papers (Labenbacher et al.)

Offline, Retraction Watch only. **By DOI: 15/15 retracted. By title only: 15/15.**

| Ref | DOI | By DOI | By title only | Title score |
|---|---|---|---|---|
| 15 | 10.1080/0167482X.2024.2318194 | retracted | retracted | 100 |
| 16 | 10.1056/NEJMoa1200303 | retracted | retracted | 100 |
| 17 | 10.1038/s41598-024-79925-8 | retracted | retracted | 100 |
| 18 | 10.1126/science.1097243 | retracted | retracted | 100 |
| 19 | 10.1016/j.ijantimicag.2020.105949 | retracted | retracted | 100 |
| 20 | 10.1016/S0140-6736(20)32656-8 | retracted | retracted | 100 |
| 21 | 10.1038/nature00870 | retracted | retracted | 100 |
| 22 | 10.1038/nature04533 | retracted | retracted | 96 |
| 23 | 10.1038/s41467-021-25387-9 | retracted | retracted | 100 |
| 24 | 10.1111/j.1751-7176.2008.07572.x | retracted | retracted | 100 |
| 25 | 10.1186/s13099-021-00398-5 | retracted | retracted | 100 |
| 26 | 10.1016/j.pmatsci.2004.04.001 | retracted | retracted | 100 |
| 27 | 10.3390/mi13060831 | retracted | retracted | 100 |
| 28 | 10.1016/j.ygyno.2008.04.033 | retracted | retracted | 100 |
| 29 | 10.1016/S0140-6736(97)11096-0 | retracted | retracted | 100 |

## 2. Retracted papers looked up by perturbed title only

Offline. **Found 50/50 (100%).** Titles were given a random perturbation: a `RETRACTED:` prefix, all caps, all lowercase, a trailing period, or colons replaced by dashes.

## 3. Non-retracted controls (random OpenAlex articles)

50 articles that OpenAlex marks `is_retracted: false`.

- **By DOI, with live fallbacks: 0/50 flagged** (statuses: no_notice 50). A flag here is not automatically wrong: Crossref may know of a notice OpenAlex lacks.
- **By title only, offline: 0/50 flagged.**


## 3b. Look-alike titles (hard false-positive probe)

For each sampled retracted paper, the closest OpenAlex title match with a different DOI that OpenAlex does not mark retracted. Checked by title only, offline.

- 50/50 OpenAlex searches failed after retries and were skipped.
- 0 of those matches were retraction notices ("Retracted: ..."), which OpenAlex indexes as separate works. Matching a notice to its paper is correct, so they are excluded.
- **Flagged: 0/0 remaining look-alikes.** Each is a same-title paper with a different DOI: an accidental duplicate, a reissued version, or a correction that reuses the title. Title-only matches are therefore reported with `certain = False`; a DOI lookup never confuses them.


## 4. Recent publisher retractions missing from the local file

50 papers whose retraction is registered with Crossref but not in the downloaded Retraction Watch CSV.

- **Offline (Retraction Watch only): 0/50 caught.**
- **With Crossref and OpenAlex fallbacks: 50/50 caught.**
