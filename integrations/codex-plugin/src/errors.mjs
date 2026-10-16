export class ReviewError extends Error {
  constructor(code, message, recovery, details = {}) {
    super(message);
    this.name = "ReviewError";
    this.code = code;
    this.recovery = recovery;
    this.details = details;
  }

  toJSON() {
    return {
      status: "ERROR",
      error_code: this.code,
      message: this.message,
      recovery: this.recovery,
      ...this.details,
    };
  }
}

export function asReviewError(error) {
  if (error instanceof ReviewError) return error;
  return new ReviewError(
    "CHATGPT_UNAVAILABLE",
    error instanceof Error ? error.message : String(error),
    "Check the visible browser and network connection, then retry.",
  );
}
