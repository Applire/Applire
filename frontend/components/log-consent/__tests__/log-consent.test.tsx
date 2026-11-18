// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Consent to the test system's logs (boards B1–B4; ADR-091 amended 2026-11-18):
 * the invite box, the interstitial after sign-in, the settings card with its
 * withdrawal dialog, the full text, and the refusal dialog. At any level above
 * DEBUG the backend says `required: false` and none of it renders.
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CurrentUserProvider, type CurrentUser, type CurrentUserValue } from "@/lib/auth";
import { handleLogConsentRefusal } from "@/lib/auth/fetch-patch";
import { LOG_CONSENT_REQUIRED_EVENT, footerDate, longDate, type LogConsentState, type LogConsentText } from "@/lib/log-consent";
import { withIntl } from "@/lib/test-utils/with-intl";
import de from "@/messages/de.json";
import en from "@/messages/en.json";

import { SetPasswordPage } from "@/app/(auth)/_components/set-password";
import { LogConsentInterstitial } from "@/app/(auth)/_components/log-consent-interstitial";
import { LogConsentCard } from "@/components/account/LogConsentCard";
import { ConsentTextBody } from "../ConsentText";
import { LogConsentDialog } from "../LogConsentDialog";

const push = vi.fn();
const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/",
}));

const TEXT: LogConsentText = {
  version: "1-abcdef12",
  text_version: "1",
  text_date: "2026-11-17",
  operator: "Tobias Rosenbaum",
  contact: "kontakt@applire.de",
  assistant_provider: "Anthropic, USA",
  log_host: "Hetzner in Deutschland",
  model_provider: "Requesty / OpenAI",
  retention_days: 30,
};
const consent = (over: Partial<LogConsentState> = {}): LogConsentState => ({
  required: true,
  state: "current",
  version: TEXT.version,
  consented_at: "2026-11-17T09:00:00Z",
  consented_version: TEXT.version,
  withdrawn_at: null,
  text: TEXT,
  ...over,
});
const ME = (log_consent: LogConsentState | null): CurrentUser => ({
  id: "u-1",
  email: "tester-01@example.org",
  role: "user",
  has_password: true,
  oidc_linked: false,
  ui_language: null,
  log_consent,
});

const realFetch = globalThis.fetch;
const realLocation = window.location;
let assign: ReturnType<typeof vi.fn>;
let calls: { url: string; method: string; body: unknown }[];
const json = (status: number, body?: unknown) => new Response(body === undefined ? null : JSON.stringify(body), { status });
function routes(handler: (url: string, method: string) => Response) {
  calls = [];
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = init.method ?? "GET";
    calls.push({ url, method, body: init.body ? JSON.parse(String(init.body)) : undefined });
    return handler(url, method);
  }) as unknown as typeof fetch;
}
function at(pathname: string, hash = "", search = "") {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...realLocation, pathname, search, hash, assign },
  });
}
function wrap(node: React.ReactNode, auth: Partial<CurrentUserValue>, locale: "de" | "en" = "de") {
  return render(withIntl(<CurrentUserProvider value={auth}>{node}</CurrentUserProvider>, locale));
}

beforeEach(() => {
  push.mockReset();
  replace.mockReset();
  assign = vi.fn();
});
afterEach(() => {
  cleanup();
  globalThis.fetch = realFetch;
  Object.defineProperty(window, "location", { configurable: true, value: realLocation });
});

// ── B1 ──────────────────────────────────────────────────────────────────────

describe("B1 — invite with the consent box", () => {
  const STATE = { setup_required: false, oidc_enabled: false, oidc_button_label: "", smtp_enabled: true, harness: false };
  const inviteRoutes = (redeem: () => Response) =>
    routes((url) =>
      url.endsWith("/inspect") ? json(200, { purpose: "invite", email: "tester-01@example.org", state: "valid" }) : redeem(),
    );
  const fillPasswords = () => {
    fireEvent.change(screen.getByLabelText(de.auth.passwordLabel), { target: { value: "a long passphrase" } });
    fireEvent.change(screen.getByLabelText(de.auth.passwordRepeatLabel), { target: { value: "a long passphrase" } });
  };

  it("at DEBUG: the mock's sentences, the box unticked, the button off until ticked, the version posted", async () => {
    at("/invite", "#tok123");
    inviteRoutes(() => new Response(null, { status: 204 }));
    wrap(<SetPasswordPage purpose="invite" />, {
      status: "unauthenticated",
      authState: { ...STATE, log_consent_required: true, log_consent_text: TEXT },
    });
    const notice = await screen.findByTestId("log-consent-notice");
    expect(notice).toHaveTextContent(
      "Das hier ist ein Testsystem. Um Fehler zu finden, speichern wir 30 Tage lang mit, was du eingibst und was die KI daraus macht. Tobias Rosenbaum und KI-Assistenten, die für ihn arbeiten, lesen diese Protokolle. Was gespeichert wird und wer es liest",
    );
    expect(notice).toHaveTextContent(
      "Ich bin einverstanden, dass meine Eingaben und die erzeugten Texte 30 Tage lang protokolliert und zur Fehlersuche gelesen werden. Ich kann das jederzeit widerrufen.",
    );
    const box = screen.getByTestId("log-consent-checkbox") as HTMLInputElement;
    expect(box.checked).toBe(false);
    fillPasswords();
    expect(screen.getByTestId("link-submit")).toBeDisabled();
    fireEvent.click(box);
    expect(screen.getByTestId("link-submit")).toBeEnabled();
    fireEvent.click(screen.getByTestId("link-submit"));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/"));
    expect(calls.find((c) => c.url.endsWith("/redeem"))?.body).toEqual({
      token: "tok123",
      password: "a long passphrase",
      log_consent_version: TEXT.version,
    });
  });

  it("the link opens the full text", async () => {
    at("/invite", "#tok123");
    inviteRoutes(() => new Response(null, { status: 204 }));
    wrap(<SetPasswordPage purpose="invite" />, {
      status: "unauthenticated",
      authState: { ...STATE, log_consent_required: true, log_consent_text: TEXT },
    });
    fireEvent.click(await screen.findByTestId("log-consent-text-link"));
    expect(screen.getByTestId("log-consent-text-dialog")).toHaveTextContent(de.logConsent.text.title);
  });

  it("above DEBUG: no block, no version in the body", async () => {
    at("/invite", "#tok123");
    inviteRoutes(() => new Response(null, { status: 204 }));
    wrap(<SetPasswordPage purpose="invite" />, { status: "unauthenticated", authState: STATE });
    await screen.findByTestId("invite-card");
    expect(screen.queryByTestId("log-consent-notice")).toBeNull();
    fillPasswords();
    fireEvent.click(screen.getByTestId("link-submit"));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/"));
    expect(calls.find((c) => c.url.endsWith("/redeem"))?.body).toEqual({ token: "tok123", password: "a long passphrase" });
  });

  it("a 422 log_consent_required (the text changed) unticks the box and says so", async () => {
    at("/invite", "#tok123");
    inviteRoutes(() => json(422, { detail: { error_code: "log_consent_required", message: "x" } }));
    const refresh = vi.fn(async () => {});
    wrap(<SetPasswordPage purpose="invite" />, {
      status: "unauthenticated",
      refresh,
      authState: { ...STATE, log_consent_required: true, log_consent_text: TEXT },
    });
    await screen.findByTestId("log-consent-notice");
    fillPasswords();
    fireEvent.click(screen.getByTestId("log-consent-checkbox"));
    fireEvent.click(screen.getByTestId("link-submit"));
    expect(await screen.findByTestId("log-consent-error")).toHaveTextContent(de.logConsent.errorStale);
    expect((screen.getByTestId("log-consent-checkbox") as HTMLInputElement).checked).toBe(false);
    expect(refresh).toHaveBeenCalled();
    expect(assign).not.toHaveBeenCalled();
  });

  it("an unset operator prints the catalog fallback, never an empty name", async () => {
    at("/invite", "#tok123");
    inviteRoutes(() => new Response(null, { status: 204 }));
    wrap(<SetPasswordPage purpose="invite" />, {
      status: "unauthenticated",
      authState: { ...STATE, log_consent_required: true, log_consent_text: { ...TEXT, operator: "", retention_days: 0 } },
    });
    const notice = await screen.findByTestId("log-consent-notice");
    expect(notice).toHaveTextContent("(vom Betreiber nicht angegeben) und KI-Assistenten");
    expect(notice).toHaveTextContent("speichern wir bis zur Löschung durch den Betreiber mit");
  });
});

// ── B2 ──────────────────────────────────────────────────────────────────────

describe("B2 — the interstitial after sign-in", () => {
  it("shows the mock's text; agree posts today's version, then goes on to next", async () => {
    at("/log-consent", "", "?next=%2Fjobs");
    routes(() => json(200, consent()));
    const refresh = vi.fn(async () => {});
    wrap(<LogConsentInterstitial />, { status: "authenticated", user: ME(consent({ state: "missing", consented_at: null })), refresh });
    const card = await screen.findByTestId("log-consent-interstitial");
    expect(card).toHaveTextContent("Bevor es weitergeht");
    expect(card).toHaveTextContent(
      "Dieses System speichert ab jetzt 30 Tage lang mit, was du eingibst und was die KI daraus macht, um Fehler zu finden. Tobias Rosenbaum und KI-Assistenten, die für ihn arbeiten, lesen diese Protokolle.",
    );
    expect(screen.getByTestId("log-consent-agree")).toBeDisabled();
    fireEvent.click(screen.getByTestId("log-consent-checkbox"));
    fireEvent.click(screen.getByTestId("log-consent-agree"));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/jobs"));
    expect(calls).toContainEqual({ url: "/api/me/log-consent", method: "POST", body: { version: TEXT.version } });
    expect(refresh).toHaveBeenCalled();
  });

  it("declining signs out", async () => {
    at("/log-consent");
    const signOut = vi.fn(async () => {});
    wrap(<LogConsentInterstitial />, { status: "authenticated", user: ME(consent({ state: "outdated" })), signOut });
    fireEvent.click(await screen.findByTestId("log-consent-decline"));
    expect(signOut).toHaveBeenCalled();
  });

  it("nothing to ask (current, or not required) → straight on", async () => {
    at("/log-consent");
    wrap(<LogConsentInterstitial />, { status: "authenticated", user: ME({ required: false, state: "not_required" }) });
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
    expect(screen.queryByTestId("log-consent-interstitial")).toBeNull();
  });
});

// ── B3 ──────────────────────────────────────────────────────────────────────

describe("B3 — the settings card and the withdrawal dialog", () => {
  it("renders nothing above DEBUG", () => {
    wrap(<LogConsentCard />, { user: ME({ required: false, state: "not_required" }) });
    expect(screen.queryByTestId("log-consent-card")).toBeNull();
  });

  it("agreed: the mock's sentence; withdraw opens the dialog with the mock's text; confirm DELETEs", async () => {
    routes(() => json(200, consent({ state: "withdrawn", withdrawn_at: "2026-11-18T10:00:00Z" })));
    const refresh = vi.fn(async () => {});
    wrap(<LogConsentCard />, { user: ME(consent()), refresh });
    expect(screen.getByTestId("log-consent-card-state")).toHaveTextContent(
      "Du hast am 17. November 2026 eingewilligt, dass deine Eingaben und die erzeugten Texte 30 Tage lang protokolliert werden.",
    );
    fireEvent.click(screen.getByTestId("log-consent-withdraw"));
    const dialog = screen.getByTestId("log-consent-withdraw-dialog");
    expect(dialog).toHaveTextContent("Einwilligung widerrufen?");
    expect(dialog).toHaveTextContent(de.logConsent.withdrawDelete);
    expect(dialog).toHaveTextContent(de.logConsent.withdrawEffect);
    fireEvent.click(screen.getByTestId("log-consent-withdraw-confirm"));
    await waitFor(() => expect(refresh).toHaveBeenCalled());
    expect(calls).toContainEqual({ url: "/api/me/log-consent", method: "DELETE", body: undefined });
  });

  it("withdrawn: says when, and offers to agree again", async () => {
    routes(() => json(200, consent()));
    const refresh = vi.fn(async () => {});
    wrap(<LogConsentCard />, {
      user: ME(consent({ state: "withdrawn", withdrawn_at: "2026-11-18T10:00:00Z" })),
      refresh,
    });
    expect(screen.getByTestId("log-consent-card-state")).toHaveTextContent(
      "Du hast deine Einwilligung am 18. November 2026 widerrufen.",
    );
    expect(screen.queryByTestId("log-consent-withdraw")).toBeNull();
    fireEvent.click(screen.getByTestId("log-consent-agree"));
    await waitFor(() => expect(refresh).toHaveBeenCalled());
    expect(calls).toContainEqual({ url: "/api/me/log-consent", method: "POST", body: { version: TEXT.version } });
  });
});

// ── B4 ──────────────────────────────────────────────────────────────────────

describe("B4 — the full text", () => {
  it("German: names the settings' values and the footer of the mock", () => {
    render(withIntl(<ConsentTextBody text={TEXT} />, "de"));
    const body = screen.getByTestId("log-consent-text");
    expect(body).toHaveTextContent("Tobias Rosenbaum, der Applire betreibt");
    expect(body).toHaveTextContent("Dabei gehen Ausschnitte aus den Protokollen an deren Anbieter: Anthropic, USA");
    expect(body).toHaveTextContent("Die Protokolle liegen auf einem Server bei Hetzner in Deutschland");
    expect(body).toHaveTextContent("30 Tage. Danach werden die Einträge automatisch gelöscht. Die Protokolle werden nicht in Sicherungskopien aufgenommen.");
    expect(body).toHaveTextContent("Verantwortlich: Tobias Rosenbaum, Applire · kontakt@applire.de · Stand 17.11.2026, Fassung 1");
  });

  it("English: the mock's footer and duration", () => {
    render(withIntl(<ConsentTextBody text={{ ...TEXT, log_host: "Hetzner in Germany" }} />, "en"));
    const body = screen.getByTestId("log-consent-text");
    expect(body).toHaveTextContent("30 days. After that the entries are deleted automatically. The logs are not included in backups.");
    expect(body).toHaveTextContent("Responsible: Tobias Rosenbaum, Applire · kontakt@applire.de · 17 Nov 2026, version 1");
  });

  it("dates follow the mock's two formats", () => {
    expect(longDate("2026-11-17T09:00:00Z", "de")).toBe("17. November 2026");
    expect(longDate("2026-11-17T09:00:00Z", "en")).toBe("17 November 2026");
    expect(footerDate("2026-11-17", "de")).toBe("17.11.2026");
    expect(footerDate("2026-11-17", "en")).toBe("17 Nov 2026");
  });

  it("every logConsent key has an English and a German value that differ where they must", () => {
    expect(Object.keys(en.logConsent).sort()).toEqual(Object.keys(de.logConsent).sort());
    expect(en.logConsent.checkbox).not.toEqual(de.logConsent.checkbox);
  });
});

// ── the refusal ─────────────────────────────────────────────────────────────

describe("409 log_consent_required → the dialog", () => {
  it("the fetch patch raises the event only for that code on an /api/ call", async () => {
    const seen = vi.fn();
    window.addEventListener(LOG_CONSENT_REQUIRED_EVENT, seen);
    const refusal = () => json(409, { detail: { error_code: "log_consent_required", message: "x" } });
    expect(await handleLogConsentRefusal(refusal(), "/api/job/analyze")).toBe(true);
    expect(await handleLogConsentRefusal(json(409, { detail: { error_code: "provider_key_required" } }), "/api/job/analyze")).toBe(false);
    expect(await handleLogConsentRefusal(json(422, { detail: { error_code: "log_consent_required" } }), "/api/x")).toBe(false);
    expect(seen).toHaveBeenCalledTimes(1);
    window.removeEventListener(LOG_CONSENT_REQUIRED_EVENT, seen);
  });

  it("the dialog opens on the event and leads to the settings card", async () => {
    const refresh = vi.fn(async () => {});
    wrap(<LogConsentDialog />, {
      user: ME(consent({ state: "withdrawn", withdrawn_at: "2026-11-18T10:00:00Z" })),
      refresh,
    });
    expect(screen.queryByTestId("log-consent-dialog")).toBeNull();
    act(() => {
      window.dispatchEvent(new CustomEvent(LOG_CONSENT_REQUIRED_EVENT));
    });
    const dialog = await screen.findByTestId("log-consent-dialog");
    expect(dialog).toHaveTextContent("Protokolle in der Testphase");
    expect(dialog).toHaveTextContent("Du hast deine Einwilligung am 18. November 2026 widerrufen.");
    fireEvent.click(screen.getByTestId("log-consent-dialog-agree"));
    expect(push).toHaveBeenCalledWith("/settings#log-consent");
  });
});
