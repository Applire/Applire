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
 * Maps the backend flow current_step to its URL sub-route segment.
 * jd_analysis maps to "" — the flow index page, which advances the state
 * machine and routes onward.
 */
export const STEP_ROUTE: Record<string, string> = {
  jd_analysis:   "",
  cv_import:     "import",
  gap_analysis:  "gaps",
  interview:     "interview",
  cv_generation: "cv",
  complete:      "cv",
};

/** Routes that are valid at any flow step and bypass the redirect guard. */
const SIDE_ROUTES = new Set(["cover-letter"]);

/**
 * Normalises a URL segment to the flow step it should highlight in the stepper.
 * The cover-letter side route is a sub-artifact of the CV result screen, so it
 * lights the same 'cv_generation' node ("4 Lebenslauf") — otherwise the
 * cover-letter page shows no active step and feels outside the flow (E038).
 */
export function activeStepSegment(currentSegment: string): string {
  return currentSegment === "cover-letter" ? "cv" : currentSegment;
}

/**
 * Decides whether the current flow URL must be corrected to match the
 * backend's current_step.
 *
 * Returns the path to redirect to, or null if the URL is already valid.
 * Unknown steps fail open (no redirect) so a newer backend can't trap an
 * older frontend in a redirect loop.
 */
export function resolveFlowRedirect(
  flowId: string,
  pathname: string,
  currentStep: string,
): string | null {
  const expectedSegment = STEP_ROUTE[currentStep];
  if (expectedSegment === undefined) return null;

  const base = `/flow/${flowId}`;
  const rest = pathname.startsWith(base) ? pathname.slice(base.length) : "";
  const currentSegment = rest.replace(/^\//, "").split("/")[0] ?? "";

  if (currentSegment === expectedSegment) return null;
  if (SIDE_ROUTES.has(currentSegment)) return null;

  return expectedSegment ? `${base}/${expectedSegment}` : base;
}
