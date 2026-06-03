#!/usr/bin/env bash
#
# Download 20 real earnings PDFs into ./pdf_input for testing the batch command.
#
# These are public investor-relations documents similar to the two bundled
# samples: Tesla-style quarterly shareholder updates, Netflix shareholder
# letters, and bank / financial earnings press releases (Citi-style).
#
# Usage:
#   bash download_test_pdfs.sh
#   python -m earnings_extractor batch pdf_input --out outputs/extractions.xlsx --mode live
#
# Notes:
#   - curl -fL skips any link that has moved instead of writing an error page,
#     so a stale URL just means one fewer file -- the batch tolerates that.
#   - Live mode needs OPENAI_API_KEY. (The batch itself will still run and the
#     Batch Status sheet will show what succeeded/failed.)

set -u
DEST="pdf_input"
mkdir -p "$DEST"

# filename|url
DOCS=(
  "tesla_q3_2025.pdf|https://assets-ir.tesla.com/tesla-contents/IR/TSLA-Q3-2025-Update.pdf"
  "tesla_q4_2025.pdf|https://assets-ir.tesla.com/tesla-contents/IR/TSLA-Q4-2025-Update.pdf"
  "tesla_q1_2026.pdf|https://assets-ir.tesla.com/tesla-contents/IR/TSLA-Q1-2026-Update.pdf"
  "netflix_q1_2025.pdf|https://s22.q4cdn.com/959853165/files/doc_financials/2025/q1/COMBINED-Q1-25-Shareholder-Letter-V2.pdf"
  "netflix_q4_2025.pdf|https://s22.q4cdn.com/959853165/files/doc_financials/2025/q4/FINAL-Q4-25-Shareholder-Letter.pdf"
  "netflix_q1_2026.pdf|https://s22.q4cdn.com/959853165/files/doc_financials/2026/q1/FINAL-Q1-26-Shareholder-Letter.pdf"
  "jpmorgan_q1_2025.pdf|https://www.jpmorganchase.com/content/dam/jpmc/jpmorgan-chase-and-co/investor-relations/documents/quarterly-earnings/2025/1st-quarter/d88c408a-bbc9-4b06-b263-373f5b10b145.pdf"
  "wells_fargo_q1_2025.pdf|https://www.wellsfargo.com/assets/pdf/about/investor-relations/earnings/first-quarter-2025-earnings.pdf"
  "wells_fargo_q1_2026.pdf|https://www.wellsfargo.com/assets/pdf/about/investor-relations/earnings/first-quarter-2026-earnings.pdf"
  "goldman_sachs_q1_2026.pdf|https://www.goldmansachs.com/pressroom/press-releases/current/pdfs/2026-q1-results.pdf"
  "morgan_stanley_q1_2025.pdf|https://www.morganstanley.com/about-us-ir/shareholder/1q2025.pdf"
  "morgan_stanley_q1_2026.pdf|https://www.morganstanley.com/about-us-ir/shareholder/1q2026.pdf"
  "citigroup_q4_2024.pdf|https://www.citigroup.com/rcs/citigpa/storage/public/Earnings/Q42024/4Q24-earnings-press-release.pdf"
  "citigroup_q4_2025.pdf|https://www.citigroup.com/rcs/citigpa/storage/public/Earnings/Q42025/2025prqtr4rslt.pdf"
  "citigroup_q1_2025_transcript.pdf|https://www.citigroup.com/rcs/citigpa/storage/public/Earnings/Q12025/transcript.pdf"
  "american_express_q1_2026.pdf|https://s26.q4cdn.com/747928648/files/doc_earnings/2026/q1/earnings-result/Q1-2026-Earnings-Press-Release.pdf"
  "charles_schwab_q1_2025.pdf|https://content.schwab.com/web/retail/public/about-schwab/schw_q1_2025_earnings_release.pdf"
  "us_bancorp_q1_2025.pdf|https://s203.q4cdn.com/711684571/files/doc_financials/2025/q1/1Q25-Earnings-Release.pdf"
  "blackrock_q1_2025.pdf|https://s24.q4cdn.com/856567660/files/doc_financials/2025/Q1/BLK-1Q25-Earning-Release.pdf"
  "blackrock_q4_2025.pdf|https://s24.q4cdn.com/856567660/files/doc_financials/2025/Q4/BLK-4Q25-Earnings-Release.pdf"
)

ok=0
fail=0
for entry in "${DOCS[@]}"; do
  name="${entry%%|*}"
  url="${entry#*|}"
  printf 'Downloading %-34s ... ' "$name"
  if curl -fLsS --retry 2 -o "$DEST/$name" "$url"; then
    echo "ok"
    ok=$((ok + 1))
  else
    echo "FAILED (skipping)"
    rm -f "$DEST/$name"
    fail=$((fail + 1))
  fi
done

echo
echo "Done: $ok downloaded, $fail skipped -> ./$DEST"
echo
echo "Next:"
echo "  python -m earnings_extractor batch pdf_input --out outputs/extractions.xlsx --mode live"
