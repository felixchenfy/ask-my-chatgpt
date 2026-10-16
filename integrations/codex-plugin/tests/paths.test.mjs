import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { validatePdfPath } from "../src/paths.mjs";

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "chatgpt-pdf-paths-"));
  const pdf = path.join(root, "technical-report.pdf");
  fs.writeFileSync(pdf, "%PDF-1.4\n");
  return { root, pdf };
}

test("accepts a readable PDF inside an allowed root", () => {
  const { root, pdf } = fixture();
  const result = validatePdfPath(pdf, [root], 1024);
  assert.equal(result.filename, "technical-report.pdf");
});

test("rejects relative paths", () => {
  assert.throws(() => validatePdfPath("report.pdf", [process.cwd()], 1024), { code: "PDF_NOT_FOUND" });
});

test("rejects non-PDF extensions", () => {
  const { root } = fixture();
  assert.throws(() => validatePdfPath(path.join(root, "report.txt"), [root], 1024), { code: "PDF_ONLY" });
});

test("rejects files outside allowed roots", () => {
  const { pdf } = fixture();
  const other = fs.mkdtempSync(path.join(os.tmpdir(), "chatgpt-pdf-other-"));
  assert.throws(() => validatePdfPath(pdf, [other], 1024), { code: "PDF_OUTSIDE_ALLOWED_ROOT" });
});

test("rejects oversized PDFs", () => {
  const { root, pdf } = fixture();
  assert.throws(() => validatePdfPath(pdf, [root], 2), { code: "PDF_TOO_LARGE" });
});
