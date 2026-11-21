#!/usr/bin/env bash
# Download the goAML schema a client's financial intelligence unit publishes, for checking reports before they are sent.
#
# goAML schemas are each FIU's own and are usually published on terms that do not allow passing them on, so CaseForge
# does not ship them. Run this on the machine that runs the platform; it saves the file where the app looks for it
# (GOAML_SCHEMA_DIR, default data/schemas/goaml). Today it knows FIU Luxembourg's (CRF) current schema; add others as
# clients need them. Read the FIU's terms of use before using its schema.
set -euo pipefail
FIU="${1:-lu-crf}"
DEST="${GOAML_SCHEMA_DIR:-$(cd "$(dirname "$0")/.." && pwd)/data/schemas/goaml}"
mkdir -p "$DEST"
case "$FIU" in
  lu-crf)
    # Source page: https://crf.public.lu/en/comment/faq/tech/sujets-it.html
    URL="https://crf.public.lu/dam-assets/faq/xsd-goaml-2026-10-19.xsd"
    FILE="xsd-goaml-2026-10-19.xsd" ;;
  *) echo "Unknown FIU profile '$FIU'. Known: lu-crf" >&2; exit 2 ;;
esac
curl -fsSL "$URL" -o "$DEST/$FILE"
echo "Saved $DEST/$FILE ($(wc -c < "$DEST/$FILE") bytes). Name it in the client's filing details as goaml_schema if it is not the default."
