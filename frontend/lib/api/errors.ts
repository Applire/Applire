// Copyright (C) 2024-2026 Tobias Rosenbaum
//
// This file is part of Applire.
//
// Applire is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published
// by the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// Applire is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with Applire. If not, see <https://www.gnu.org/licenses/>.

/**
 * Detects raw framework/validation noise that must never reach a user (F3, #72).
 *
 * Pydantic surfaces messages like "Input should be a valid UUID, invalid
 * character … For further information visit https://errors.pydantic.dev/…" and
 * "X validation errors for …". These leak internal ids/types and frighten the
 * non-engineer persona, so we suppress them in favour of a friendly fallback.
 */
export function isLeakyDetail(detail: string): boolean {
  const d = detail.toLowerCase();
  return (
    d.includes("errors.pydantic.dev") ||
    d.includes("valid uuid") ||
    d.includes("validation error") ||
    /\binput should be\b/.test(d) ||
    /\binput is too (short|long)\b/.test(d) ||
    d.includes("input_value=")
  );
}

/**
 * Translates HTTP error codes into user-friendly messages.
 * Never exposes raw error details to users for security.
 */
export function translateApiError(status: number, detail?: string): string {
  // Treat empty/suppressed detail as absent so the generic fallback applies.
  const safeDetail = detail && detail.trim() ? detail : undefined;
  switch (status) {
    case 504:
      return "This is taking longer than usual. Please try again.";
    case 503:
      return "Service temporarily busy. Please wait a moment and retry.";
    case 502:
      return "Could not parse this format. Please try a different file.";
    case 401:
      return "Session expired. Please refresh the page.";
    case 409:
      return safeDetail ?? "This action conflicts with the current state.";
    case 422:
      return safeDetail ?? "Invalid input. Please check your entries.";
    case 404:
      return "The requested resource was not found.";
    case 429:
      return "Too many requests. Please wait a moment and try again.";
    case 500:
      return "An internal error occurred. Please try again later.";
    default:
      return safeDetail ?? `An error occurred (${status}). Please try again.`;
  }
}

/**
 * Extracts error message from API response.
 * Safely handles various response formats.
 */
export async function extractApiError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = body.detail;

    let message: string;
    if (typeof detail === "string") {
      message = detail;
    } else if (Array.isArray(detail)) {
      message = detail
        .map((e: { msg?: string }) => e.msg ?? JSON.stringify(e))
        .join("; ");
    } else if (detail?.message) {
      message = detail.message;
    } else {
      return res.statusText || `HTTP ${res.status}`;
    }

    // Suppress raw pydantic/validation spew so it can never reach the user;
    // translateApiError then falls back to a status-based friendly message.
    if (isLeakyDetail(message)) {
      return "";
    }
    return message;
  } catch {
    return res.statusText || `HTTP ${res.status}`;
  }
}

/**
 * Combines error extraction and translation.
 */
export async function getApiErrorMessage(res: Response): Promise<string> {
  const detail = await extractApiError(res);
  return translateApiError(res.status, detail);
}