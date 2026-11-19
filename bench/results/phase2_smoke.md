# Phase 2 smoke test: baseline RAG

30 questions on non-coding RNAs in cancer, k = 8 passages each, over the 2,500-paper / 85,754-chunk corpus. Hybrid retrieval (BM25 + bge-small-en-v1.5, reciprocal rank fusion, bge-reranker-base, at most 2 passages per paper).

"Flagged" means the Phase 1 integrity checker (Retraction Watch, plus live Crossref and OpenAlex lookups) records the passage's paper as retracted or under an expression of concern. The baseline pipeline does not use this information; it is measured here to size the problem Phase 3 addresses.

## Summary

- Retrieval latency: median 0.78s, max 0.94s per question (GPU).
- **Questions retrieving at least one flagged passage: 19/30.**
- Flagged passages: 37/237 (16%).
- Context: 506 of the corpus's 2,500 papers (20%) are flagged, because the corpus deliberately includes 500 papers Europe PMC tags as retracted. These rates reflect that mix, not the prevalence of retracted work in the literature at large; what they show is that the baseline retrieves flagged papers about as readily as sound ones.
- Answer generation **not run**: no Claude credentials were found. Set `ANTHROPIC_API_KEY` and rerun to complete the citation check.

## Per question

| # | Question | Retrieved | Flagged | Citations | Citing flagged | Problems |
|---|---|---|---|---|---|---|
| 1 | What role does miR-21 play in tumour cell proliferation and apoptosis? | 8 (6 papers) | 1 | - | - | - |
| 2 | How does the lncRNA MALAT1 contribute to cancer metastasis? | 8 (6 papers) | 0 | - | - | - |
| 3 | Does HOTAIR promote epithelial-mesenchymal transition, and through which mechanism? | 8 (7 papers) | 2 | - | - | - |
| 4 | What is known about the let-7 family as tumour suppressors? | 8 (7 papers) | 0 | - | - | - |
| 5 | How do circular RNAs act as microRNA sponges in gastric cancer? | 8 (5 papers) | 2 | - | - | - |
| 6 | Does circHIPK3 promote colorectal cancer growth and metastasis? | 5 (3 papers) | 3 | - | - | - |
| 7 | Which microRNAs are proposed as diagnostic biomarkers in serum for hepatocellular carcinoma? | 8 (6 papers) | 0 | - | - | - |
| 8 | What is the role of the lncRNA H19 in cancer drug resistance? | 8 (5 papers) | 1 | - | - | - |
| 9 | How do exosomal microRNAs mediate communication between tumour cells and their microenvironment? | 8 (7 papers) | 0 | - | - | - |
| 10 | Which microRNAs regulate PTEN expression in cancer? | 8 (7 papers) | 1 | - | - | - |
| 11 | What is the function of the miR-200 family in epithelial-mesenchymal transition? | 8 (6 papers) | 0 | - | - | - |
| 12 | How does lncRNA PVT1 interact with MYC in cancer? | 8 (5 papers) | 1 | - | - | - |
| 13 | Are circulating microRNAs useful for early detection of lung cancer? | 8 (8 papers) | 0 | - | - | - |
| 14 | How does miR-34a connect to the p53 pathway? | 8 (6 papers) | 0 | - | - | - |
| 15 | What mechanisms link lncRNA XIST to tumour progression? | 8 (7 papers) | 3 | - | - | - |
| 16 | Which non-coding RNAs are associated with cisplatin resistance in ovarian cancer? | 8 (6 papers) | 1 | - | - | - |
| 17 | How does m6A RNA methylation affect non-coding RNA function in cancer? | 8 (7 papers) | 1 | - | - | - |
| 18 | What is the role of miR-155 in lymphoma and immune regulation? | 8 (6 papers) | 0 | - | - | - |
| 19 | Can microRNA-based therapeutics be delivered effectively to tumours in vivo? | 8 (8 papers) | 0 | - | - | - |
| 20 | How does lncRNA NEAT1 influence cancer cell survival? | 8 (8 papers) | 0 | - | - | - |
| 21 | Which circular RNAs are implicated in hepatocellular carcinoma progression? | 8 (5 papers) | 5 | - | - | - |
| 22 | What evidence links miR-145 to suppression of cancer stem cell properties? | 8 (7 papers) | 2 | - | - | - |
| 23 | How do competing endogenous RNA networks regulate oncogenes in breast cancer? | 8 (8 papers) | 1 | - | - | - |
| 24 | What is the role of lncRNA ANRIL in cancer through PRC2 recruitment? | 8 (6 papers) | 4 | - | - | - |
| 25 | Which microRNAs target the Wnt/beta-catenin pathway in colorectal cancer? | 8 (6 papers) | 1 | - | - | - |
| 26 | How does lncRNA TUG1 affect proliferation in glioma? | 8 (7 papers) | 2 | - | - | - |
| 27 | What role do microRNAs play in resistance to EGFR inhibitors in lung cancer? | 8 (7 papers) | 0 | - | - | - |
| 28 | Is miR-375 a tumour suppressor or an oncogene? | 8 (6 papers) | 2 | - | - | - |
| 29 | How does circRNA CDR1as (ciRS-7) regulate miR-7 in cancer? | 8 (6 papers) | 3 | - | - | - |
| 30 | What are the limitations of using cell-line studies to establish lncRNA function in cancer? | 8 (8 papers) | 1 | - | - | - |
