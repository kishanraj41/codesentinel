"""Shared code terms, context cues, and drug specificity.

The offline cascade uses these lists as a stand-in triage policy. It does not
read gold labels. Live runs send the chart to Nemotron instead.
"""

from __future__ import annotations

# Terms that tie an ICD-10 code to a sentence in the note.
CODE_TERMS: dict[str, tuple[str, ...]] = {
    "E11.65": ("diabetes", "diabetic", "hyperglycemia"),
    "E11.9": ("diabetes", "diabetic"),
    "E11.22": ("diabetic nephropathy", "diabetic kidney", "diabetic ckd", "diabetic chronic kidney"),
    "N18.3": ("chronic kidney", "ckd", "kidney disease"),
    "N18.4": ("chronic kidney", "ckd", "kidney disease"),
    "N18.5": ("chronic kidney", "ckd", "kidney disease"),
    "N18.9": ("chronic kidney", "ckd", "kidney disease"),
    "I50.9": ("heart failure", "chf", "congestive"),
    "I50.22": ("heart failure", "reduced ejection", "systolic", "hfref"),
    "I50.20": ("heart failure", "systolic"),
    "J44.1": ("copd", "chronic obstructive", "emphysema"),
    "E03.9": ("hypothyroid", "underactive thyroid"),
    "E78.5": ("hyperlipidemia", "dyslipidemia", "high cholesterol"),
    "E78.0": ("hypercholesterolemia", "high cholesterol"),
    "I10": ("hypertension", "high blood pressure", "htn"),
    "I12.9": ("hypertensive ckd", "hypertensive chronic kidney", "hypertensive kidney"),
    "D64.9": ("anemia", "anemic"),
    "D50.9": ("iron deficiency", "ferritin"),
    "E55.9": ("vitamin d",),
    "E79.0": ("uric acid", "hyperuricemia"),
    "F32.9": ("major depressive", "depressive disorder", "mdd"),
    "F41.1": ("generalized anxiety", "gad"),
    "G47.33": ("sleep apnea", "cpap", "osa"),
    "I48.91": ("atrial fibrillation", "afib", "a-fib"),
    "K21.0": ("gerd", "acid reflux", "heartburn", "esophagitis"),
    "K76.89": ("fatty liver", "nafld", "cirrhosis", "liver disease"),
    "G62.9": ("peripheral neuropathy", "neuropathic"),
    "I63.9": ("stroke", "cerebral infarct"),
    "J45.20": ("asthma",),
}

# A combination code is "affirmed" only when the note links the parents.
LINK_PHRASES: dict[str, tuple[str, ...]] = {
    "E11.22": (
        "diabetic nephropathy",
        "diabetic ckd",
        "diabetic kidney",
        "diabetic chronic kidney",
        "diabetes with chronic kidney",
    ),
    "I12.9": (
        "hypertensive ckd",
        "hypertensive chronic kidney",
        "hypertensive kidney",
        "hypertension with chronic kidney",
    ),
}

# Cue lists are applied to the sentence that contains the code's terms.
# Bare "not" / "no" / "without" are intentionally absent: they fire on
# ordinary phrases such as "diabetes without complication".
NEGATION_CUES: tuple[str, ...] = (
    "does not have",
    "do not have",
    "did not have",
    "no longer has",
    "denies",
    "denied",
    "no evidence of",
    "no history of",
    "no sign of",
    "no signs of",
    "negative for",
    "not an active",
    "in error",
    "entered in error",
    "was retracted",
    "never smoked",
    "never had",
    "never diagnosed",
    "this is not",
    "please remove",
    "do not use",
)

FAMILY_CUES: tuple[str, ...] = (
    "mother",
    "father",
    "sister",
    "brother",
    "grandmother",
    "grandfather",
    "maternal",
    "paternal",
    "family story",
    "family history",
)

RESOLVED_CUES: tuple[str, ...] = (
    "resolved",
    "remote history",
    "historically resolved",
    "no longer active",
)

HYPOTHETICAL_CUES: tuple[str, ...] = (
    "evaluate for",
    "rule out",
    "ruled out",
    "possible",
    "if the cough persists",
)

SCREENING_CUES: tuple[str, ...] = (
    "routine item",
    "screening for",
    "as a screening",
    "preventive only",
)

# Drugs that support a code on their own (medication-only → human review)
# versus drugs the rules treat as markers but that do not establish the code.
HIGH_SPECIFICITY_DRUGS: dict[str, tuple[str, ...]] = {
    "E11.9": ("metformin", "insulin", "glipizide", "glyburide", "glimepiride"),
    "E11.65": ("metformin", "insulin", "glipizide", "glyburide", "glimepiride"),
    "E78.5": ("atorvastatin", "rosuvastatin", "simvastatin", "pravastatin", "lovastatin", "pitavastatin"),
    "E78.0": ("atorvastatin", "rosuvastatin", "simvastatin", "pravastatin", "lovastatin", "pitavastatin"),
    "E03.9": ("levothyroxine", "synthroid", "liothyronine"),
    "J44.1": ("tiotropium", "umeclidinium", "spiriva"),
    "I10": ("lisinopril", "amlodipine", "losartan", "valsartan", "hydrochlorothiazide", "hctz"),
}

# Rules phenotype markers that are not sufficient for these codes.
LOW_SPECIFICITY_DRUGS: dict[str, tuple[str, ...]] = {
    "N18.9": (
        "lisinopril", "losartan", "valsartan", "enalapril", "ramipril", "irbesartan",
        "benazepril", "amlodipine", "candesartan", "telmisartan", "olmesartan",
    ),
    "N18.3": (
        "lisinopril", "losartan", "valsartan", "enalapril", "ramipril", "amlodipine",
    ),
    "K21.0": ("omeprazole", "pantoprazole", "esomeprazole", "lansoprazole", "famotidine"),
    "F32.9": (
        "sertraline", "fluoxetine", "escitalopram", "citalopram", "paroxetine",
        "venlafaxine", "duloxetine", "bupropion", "mirtazapine", "trazodone",
    ),
    "I50.9": ("carvedilol", "metoprolol", "bisoprolol"),
}

CATALOG_PRICES_PER_MILLION: dict[str, tuple[float, float]] = {
    # input, output USD per 1M tokens. Token Factory public catalog, 2026-10-24 brief.
    "nvidia/Nemotron-3_5-Lightning": (0.06, 0.24),
    "nvidia/nemotron-3-super-120b-a12b": (0.30, 0.90),
    "nvidia/Nemotron-3-Ultra-550b-a55b": (1.00, 3.00),
}

MODEL_BY_STAGE: dict[str, str] = {
    "triage": "nvidia/Nemotron-3_5-Lightning",
    "adjudicate": "nvidia/nemotron-3-super-120b-a12b",
    "escalate": "nvidia/Nemotron-3-Ultra-550b-a55b",
}

CONFIGS: tuple[str, ...] = ("C0", "C0+", "C1", "C2", "C3", "C4", "C4a")

CONFIG_LABELS: dict[str, str] = {
    "C0": "Rules only, approved bucket",
    "C0+": "Rules only, approved + review",
    "C1": "Rules + Lightning triage + quote verifier",
    "C2": "C1 + Super adjudication, no Tavily",
    "C3": "C2 + Tavily guideline grounding",
    "C4": "C3 + Ultra escalation (full cascade)",
    "C4a": "Adaptive: C1, full C4 when the chart has a combination/specificity candidate",
}
