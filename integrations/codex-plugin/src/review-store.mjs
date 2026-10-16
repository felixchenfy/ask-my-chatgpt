import fs from "node:fs";
import path from "node:path";
import { ReviewError } from "./errors.mjs";

const TRANSITIONS = Object.freeze({
  PREPARED: new Set(["SUBMITTING"]),
  SUBMITTING: new Set(["SUBMITTED", "COMPLETED", "STOPPED", "TIMEOUT"]),
  SUBMITTED: new Set(["COMPLETED", "STOPPED", "ERROR", "TIMEOUT"]),
  COMPLETED: new Set(),
  STOPPED: new Set(),
  ERROR: new Set(),
  TIMEOUT: new Set(["COMPLETED", "TIMEOUT"]),
});

function safeId(filename, now) {
  const stem = path.basename(filename, path.extname(filename))
    .replace(/[^a-zA-Z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48) || "pdf-review";
  const stamp = now.toISOString().replace(/[-:]/g, "").replace("T", "-").slice(0, 15);
  return `${stem}-${stamp}`;
}

export class ReviewStore {
  constructor(directory, clock = () => new Date()) {
    this.directory = directory;
    this.clock = clock;
    fs.mkdirSync(directory, { recursive: true });
  }

  create(data) {
    const id = safeId(data.pdfFilename, this.clock());
    if (fs.existsSync(this.file(id))) {
      throw new ReviewError(
        "PREPARED_REVIEW_COLLISION",
        `A prepared review named ${id} already exists.`,
        "Wait one second and prepare the review again.",
      );
    }
    const record = {
      preparedReviewId: id,
      status: "PREPARED",
      createdAt: this.clock().toISOString(),
      sendAttempts: 0,
      ...data,
    };
    this.write(record);
    return record;
  }

  file(id) {
    if (!/^[a-zA-Z0-9_-]+$/.test(id)) {
      throw new ReviewError(
        "PREPARED_REVIEW_NOT_FOUND",
        "The prepared review identifier is invalid.",
        "Use the exact prepared_review_id returned by the prepare tool.",
      );
    }
    return path.join(this.directory, `${id}.json`);
  }

  get(id) {
    const filePath = this.file(id);
    if (!fs.existsSync(filePath)) {
      throw new ReviewError(
        "PREPARED_REVIEW_NOT_FOUND",
        `Prepared review ${id} was not found.`,
        "Prepare the PDF review again.",
      );
    }
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  }

  transition(id, nextStatus, changes = {}) {
    const record = this.get(id);
    if (!TRANSITIONS[record.status]?.has(nextStatus)) {
      throw new ReviewError(
        record.status === "PREPARED" ? "REVIEW_NOT_SUBMITTED" : "DUPLICATE_SEND_PREVENTED",
        `Cannot move prepared review from ${record.status} to ${nextStatus}.`,
        record.status === "PREPARED"
          ? "Submit the prepared review first."
          : "Do not resend it; use wait_for_chatgpt_review for the existing submission.",
      );
    }
    const updated = {
      ...record,
      ...changes,
      status: nextStatus,
      updatedAt: this.clock().toISOString(),
    };
    this.write(updated);
    return updated;
  }

  write(record) {
    const target = this.file(record.preparedReviewId);
    const temporary = `${target}.writing`;
    fs.writeFileSync(temporary, `${JSON.stringify(record, null, 2)}\n`, { flag: "w" });
    fs.renameSync(temporary, target);
  }
}
