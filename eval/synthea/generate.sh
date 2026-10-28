#!/usr/bin/env bash
# Generate the Synthea population used by the benchmark. Offline after the jar download.
# Needs Java 17+ (tested with Temurin 17.0.21).
set -euo pipefail
OUT="${1:-synthea-out}"
JAR="${SYNTHEA_JAR:-synthea-v4.0.0.jar}"
SHA=ed43c20ad40ba5c3bc724503a5af032715fe3c491620b766148e7c2361e6ecc1
if [ ! -f "$JAR" ]; then
  curl -sSL -o "$JAR" https://github.com/synthetichealth/synthea/releases/download/v4.0.0/synthea-with-dependencies.jar
fi
echo "$SHA  $JAR" | sha256sum -c -
java -Xmx6g -jar "$JAR" \
  -s 20261027 -cs 20261027 -r 20261027 -p 400 -a 45-90 \
  --exporter.baseDirectory="$OUT/" \
  --exporter.fhir.export=true \
  --exporter.hospital.fhir.export=false \
  --exporter.practitioner.fhir.export=false \
  --exporter.years_of_history=10 \
  --exporter.csv.export=false \
  --exporter.ccda.export=false \
  --generate.only_alive_patients=true \
  Texas
