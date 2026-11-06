# Local qualification candidate. Publication approval requires a reviewed digest.
FROM python@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b
RUN ["python3", "-I", "-c", "import os; os.mkdir('/work'); open('/work/.producer-volume', 'wb').write(b'delivery-review-private-volume-v1\\n'); os.chown('/work', 65532, 65532); os.chmod('/work', 0o700)"]
USER 65532:65532
WORKDIR /work
ENV HOME=/work/home TMPDIR=/work/tmp
