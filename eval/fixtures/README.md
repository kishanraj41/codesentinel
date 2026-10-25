# Eval fixtures

`recorded/` stores Token Factory and Tavily responses captured during a live run, keyed by a hash of the request. Those JSON files are gitignored. Replay uses them when they exist and `EVAL_IGNORE_RECORDED` is unset. Nothing in this directory is required for the offline run.

`fixtures/tavily_cache.json` is the live search cache and is gitignored.

Offline guideline text is `GUIDELINE_FIXTURES` in `eval/policy.py`: short original descriptions of coding conventions plus the public CMS URL a live search would retrieve. Those strings are not quotations from the ICD-10-CM Official Guidelines.
