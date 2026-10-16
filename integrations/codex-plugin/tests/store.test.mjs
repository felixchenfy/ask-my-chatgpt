import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { ReviewStore } from "../src/review-store.mjs";

function store() {
  return new ReviewStore(
    fs.mkdtempSync(path.join(os.tmpdir(), "chatgpt-review-store-")),
    () => new Date("2026-08-21T12:34:56Z"),
  );
}

test("uses readable prepared review identifiers", () => {
  const record = store().create({ pdfFilename: "Perception Report.pdf" });
  assert.equal(record.preparedReviewId, "Perception-Report-20260821-123456");
});

test("enforces valid state transitions", () => {
  const reviews = store();
  const record = reviews.create({ pdfFilename: "report.pdf" });
  reviews.transition(record.preparedReviewId, "SUBMITTING");
  reviews.transition(record.preparedReviewId, "SUBMITTED");
  assert.throws(
    () => reviews.transition(record.preparedReviewId, "SUBMITTED"),
    { code: "DUPLICATE_SEND_PREVENTED" },
  );
});
