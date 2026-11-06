# delivery-review authored reference fixture

This small Apache-2.0 Python project is an explicitly authored qualification
subject. It contains no customer code or private evidence. It is not a benchmark
of real-world correctness or an independent maintainer reproduction.

The `base` version uses a loop to sum valid quantities. The `e01` version uses
`sum` with the same validation and unchanged four tests. Other deliberately
failing, skipped or empty variants must be identified as qualification controls.

The Actions consumer calls one approved full-SHA revision of the delivery-review
producer. That workflow runs only literal `tests` in its isolated guest and
publishes a measured receipt/JUnit artifact with a separate signed attestation.
It does not install this repository or use its dependency files on the host.
`requirements.txt` is provided only for a maintainer's intentional local test run.

No generated manifest or successful workflow is a delivery-review acceptance
receipt. A real inspection must independently verify the selected execution,
producer qualification, original artifact, signature, source and pinned engines.
Approval of the complete inspection policy belongs outside this repository's PR.

For intentional local execution in a new virtual environment:

```sh
python -m pip install --require-hashes --no-deps --only-binary=:all: -r requirements.txt
python -m pytest tests
```

The full procedure and declared negative controls live in the parent
[delivery-review reference documentation](https://github.com/taipei49314/delivery-review/tree/main/examples/reference).
