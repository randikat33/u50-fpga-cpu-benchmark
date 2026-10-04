# Blind re-coding sheet for the literature audit

The audit in the paper (30 papers, 9 items) was coded by **one** author; no second coding was done, and the paper states this as a limitation. These files let a reader re-code the audit independently and measure the agreement.

1. Open `S1_second_coder_blank.xlsx`. Read the sheets `README` and `codebook` first.
2. Work **blind**: do not open `S1_literature_audit.xlsx` or `coding.csv` until you have finished.
3. On the sheet `coding`, papers are in a random order. For each paper:
   - open the link;
   - choose a code for each item from the drop-down;
   - paste the supporting quote.
4. Time: about 20–30 minutes per paper. The first 12 rows are a random 40% subset.
5. Save the file as `S1_second_coder_<initials>.xlsx`.

`python3 audit_kappa.py S1_second_coder_<initials>.xlsx` gives per-item Cohen's κ with 95% confidence intervals, PABAK and a list of disagreements (`disagreements.csv`).
