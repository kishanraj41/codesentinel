# Recorded Nemotron completions

Raw Nebius Token Factory completions from the published live run: `content`, `usage`, and `model` per call. The file name is a SHA-256 of the request. Request headers and keys are not stored.

To replay on the commit named in `../RESULTS.md`, copy them to `eval/fixtures/nemotron/` and run the live path with `CODESENTINEL_REPLAY=1`. A request that is not in the recording is sent to Token Factory.
