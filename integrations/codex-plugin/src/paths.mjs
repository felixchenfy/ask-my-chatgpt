import fs from "node:fs";
import path from "node:path";
import { ReviewError } from "./errors.mjs";

function comparable(value) {
  const normalized = path.resolve(value).replace(/[\\/]+$/, "");
  return process.platform === "win32" ? normalized.toLowerCase() : normalized;
}

function isWithin(filePath, rootPath) {
  const file = comparable(filePath);
  const root = comparable(rootPath);
  return file === root || file.startsWith(`${root}${path.sep}`);
}

export function validatePdfPath(pdfPath, allowedRoots, maximumBytes) {
  if (!path.isAbsolute(pdfPath)) {
    throw new ReviewError(
      "PDF_NOT_FOUND",
      "The PDF path must be absolute.",
      "Pass the full local path to the PDF.",
    );
  }
  if (path.extname(pdfPath).toLowerCase() !== ".pdf") {
    throw new ReviewError(
      "PDF_ONLY",
      "Only .pdf files are accepted.",
      "Choose a PDF or deliberately extend the plugin policy before uploading another file type.",
    );
  }

  let realFile;
  let stats;
  try {
    realFile = fs.realpathSync.native(pdfPath);
    stats = fs.statSync(realFile);
  } catch {
    throw new ReviewError(
      "PDF_NOT_FOUND",
      `The PDF was not found or is unreadable: ${pdfPath}`,
      "Check the path and file permissions, then retry.",
    );
  }
  if (!stats.isFile()) {
    throw new ReviewError(
      "PDF_NOT_FOUND",
      "The PDF path is not a regular file.",
      "Choose a readable PDF file, not a directory or device.",
    );
  }

  const realRoots = allowedRoots
    .filter((root) => fs.existsSync(root))
    .map((root) => fs.realpathSync.native(root));
  if (!realRoots.some((root) => isWithin(realFile, root))) {
    throw new ReviewError(
      "PDF_OUTSIDE_ALLOWED_ROOT",
      `The PDF is outside the configured allowed directories: ${realFile}`,
      "Move it into an allowed directory or update config.json deliberately.",
      { allowed_roots: allowedRoots },
    );
  }
  if (stats.size > maximumBytes) {
    throw new ReviewError(
      "PDF_TOO_LARGE",
      `The PDF is ${stats.size} bytes; the configured limit is ${maximumBytes} bytes.`,
      "Use a smaller PDF or lower-resolution export, or deliberately update the configured limit.",
    );
  }
  return { path: realFile, filename: path.basename(realFile), sizeBytes: stats.size };
}
