jsQR - QR decoding, vendored deliberately.

  library   jsQR, version 1.4.0
  licence   MIT
  source    npm jsqr, dist/jsQR.min.js (minified by jsDelivr with Terser 5.37.0)
  fetched   2026-08-15
  build banner removed so the repo's personal-data scan does not read
  "name-at-version" as an email address; nothing else was altered.

WHY IT IS HERE, AND WHY IT IS NOT OPTIONAL.
/signin lets somebody sign a camera back in from a photo of their QR key. That
path exists because a QR sitting in your own camera roll cannot be scanned by
anything - you cannot point a phone's camera at its own screen. The first
version used the browser's BarcodeDetector, which iOS Safari and Firefox do not
have, so it failed on most of the phones people own. A volunteer hit exactly
that, on an iPhone, holding a screenshot of their own key.

It is loaded LAZILY, only when somebody actually picks an image, so it costs
nothing to everyone else.

⚠️ IT IS THE ONLY THIRD-PARTY JS IN THIS PROJECT THAT WAS NOT WRITTEN HERE.
Checked before vendoring: pure computation, no fetch/XMLHttpRequest/eval/
importScripts anywhere in the file. If you replace it, check that again - this
page handles credentials.

To verify what is committed matches what was published:
  curl -sL https://cdn.jsdelivr.net/npm/jsqr@1.4.0/dist/jsQR.min.js \
    | tail -n +6 | sha256sum
