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
 * FirstUseExplainer + useExplainer — #679, the generic once-per-user explainer.
 *
 * The mechanism is two halves and both are pinned here: the card (shape, copy,
 * suppression checkbox, Escape) and the hook that decides whether it appears at
 * all. D-3 governs the hook: `GET /api/settings` failing or being slow means the
 * explainer SHOWS — an explanation is never swallowed by an unreachable
 * preference, and the read never gates the control behind it.
 */
import { useState } from "react";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { FirstUseExplainer } from "../FirstUseExplainer";
import { useExplainer } from "../useExplainer";
import { withIntl } from "@/lib/test-utils/with-intl";

const ID = "fact_pins_intro";

function settingsBody(dismissed?: string[]) {
  return {
    default_color_profile_id: null,
    default_accent_hex: null,
    ui_language: "en",
    hide_predownload_notice: false,
    target_cv_pages: null,
    ...(dismissed === undefined ? {} : { dismissed_explainers: dismissed }),
  };
}

/** GET /api/settings answers with `get`; PATCH is recorded and echoes back. */
function mockSettingsFetch(get: { ok: boolean; body?: unknown } | "throw" | "never") {
  const patches: unknown[] = [];
  let resolveNever: (() => void) | null = null;
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (!url.endsWith("/api/settings")) throw new Error(`Unexpected fetch: ${method} ${url}`);
    if (method === "PATCH") {
      patches.push(JSON.parse(init!.body as string));
      return { ok: true, status: 200, json: async () => settingsBody([ID]) } as Response;
    }
    if (get === "throw") throw new Error("network error");
    if (get === "never") {
      // A read that never settles — the D-3 "slow" case.
      return new Promise<Response>(() => {
        resolveNever = () => {};
      });
    }
    return { ok: get.ok, json: async () => get.body } as Response;
  });
  vi.stubGlobal("fetch", fn);
  void resolveNever;
  return { fn, patches };
}

/**
 * The real wiring: a control that opens the explainer on click, then the
 * picker. Mirrors how `PinnedFactsPanel` uses the pair.
 */
function Harness({ onOpenPicker }: { onOpenPicker: () => void }) {
  const { shouldShow, dismiss } = useExplainer(ID);
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button
        type="button"
        data-testid="open"
        onClick={() => {
          if (shouldShow) setOpen(true);
          else onOpenPicker();
        }}
      >
        {"open"}
      </button>
      {open && (
        <FirstUseExplainer
          explainerId={ID}
          title="Before you pin facts"
          paragraphs={["one", "two", "three"]}
          continueLabel="Continue to selection"
          canSuppress
          onContinue={(dontShowAgain) => {
            if (dontShowAgain) dismiss();
            setOpen(false);
            onOpenPicker();
          }}
          onCancel={() => setOpen(false)}
        />
      )}
    </div>
  );
}

describe("FirstUseExplainer", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the title, every paragraph and the two actions", async () => {
    render(
      withIntl(
        <FirstUseExplainer
          explainerId={ID}
          title="Before you pin facts"
          paragraphs={["one", "two", "three"]}
          continueLabel="Continue to selection"
          canSuppress
          onContinue={() => {}}
          onCancel={() => {}}
        />,
      ),
    );
    expect(screen.getByTestId(`explainer-${ID}`)).toBeInTheDocument();
    expect(screen.getByTestId(`explainer-${ID}-p1`).textContent).toBe("one");
    expect(screen.getByTestId(`explainer-${ID}-p3`).textContent).toBe("three");
    expect(screen.getByTestId(`explainer-${ID}-continue`).textContent).toBe(
      "Continue to selection",
    );
    expect(screen.getByTestId(`explainer-${ID}-cancel`).textContent).toBe("Cancel");
  });

  it("focuses the primary action so Enter continues", () => {
    render(
      withIntl(
        <FirstUseExplainer
          explainerId={ID}
          title="t"
          paragraphs={["one"]}
          continueLabel="Continue"
          canSuppress
          onContinue={() => {}}
          onCancel={() => {}}
        />,
      ),
    );
    expect(document.activeElement).toBe(screen.getByTestId(`explainer-${ID}-continue`));
  });

  it("Escape cancels", () => {
    const onCancel = vi.fn();
    render(
      withIntl(
        <FirstUseExplainer
          explainerId={ID}
          title="t"
          paragraphs={["one"]}
          continueLabel="Continue"
          canSuppress
          onContinue={() => {}}
          onCancel={onCancel}
        />,
      ),
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onCancel).toHaveBeenCalled();
  });

  it("hides the suppression checkbox when canSuppress is false", () => {
    render(
      withIntl(
        <FirstUseExplainer
          explainerId={ID}
          title="t"
          paragraphs={["one"]}
          continueLabel="Continue"
          canSuppress={false}
          onContinue={() => {}}
          onCancel={() => {}}
        />,
      ),
    );
    expect(screen.queryByTestId(`explainer-${ID}-dontshowagain`)).toBeNull();
  });

  it("passes the checkbox state to onContinue", () => {
    const onContinue = vi.fn();
    render(
      withIntl(
        <FirstUseExplainer
          explainerId={ID}
          title="t"
          paragraphs={["one"]}
          continueLabel="Continue"
          canSuppress
          onContinue={onContinue}
          onCancel={() => {}}
        />,
      ),
    );
    fireEvent.click(screen.getByTestId(`explainer-${ID}-continue`));
    expect(onContinue).toHaveBeenCalledWith(false);

    fireEvent.click(screen.getByTestId(`explainer-${ID}-dontshowagain-input`));
    fireEvent.click(screen.getByTestId(`explainer-${ID}-continue`));
    expect(onContinue).toHaveBeenLastCalledWith(true);
  });

  it("localises its own chrome into German", () => {
    render(
      withIntl(
        <FirstUseExplainer
          explainerId={ID}
          title="Bevor du Fakten festlegst"
          paragraphs={["eins"]}
          continueLabel="Weiter zur Auswahl"
          canSuppress
          onContinue={() => {}}
          onCancel={() => {}}
        />,
        "de",
      ),
    );
    expect(screen.getByTestId(`explainer-${ID}-dontshowagain`).textContent).toContain(
      "Nicht mehr anzeigen",
    );
    expect(screen.getByTestId(`explainer-${ID}-cancel`).textContent).toBe("Abbrechen");
  });
});

describe("useExplainer (D-3)", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the explainer when the id is not in dismissed_explainers", async () => {
    mockSettingsFetch({ ok: true, body: settingsBody([]) });
    const onOpenPicker = vi.fn();
    render(withIntl(<Harness onOpenPicker={onOpenPicker} />));
    await waitFor(() => expect(fetch).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId("open"));
    expect(screen.getByTestId(`explainer-${ID}`)).toBeInTheDocument();
    expect(onOpenPicker).not.toHaveBeenCalled();
  });

  it("skips the explainer once the id IS in dismissed_explainers", async () => {
    mockSettingsFetch({ ok: true, body: settingsBody([ID]) });
    const onOpenPicker = vi.fn();
    render(withIntl(<Harness onOpenPicker={onOpenPicker} />));
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    // Let the settings promise chain settle before the click.
    await act(async () => {});

    fireEvent.click(screen.getByTestId("open"));
    expect(screen.queryByTestId(`explainer-${ID}`)).toBeNull();
    expect(onOpenPicker).toHaveBeenCalled();
  });

  it("shows the explainer when the settings read fails (fail-open)", async () => {
    mockSettingsFetch("throw");
    render(withIntl(<Harness onOpenPicker={() => {}} />));
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    await act(async () => {});

    fireEvent.click(screen.getByTestId("open"));
    expect(screen.getByTestId(`explainer-${ID}`)).toBeInTheDocument();
  });

  it("shows the explainer while the settings read is still in flight", async () => {
    mockSettingsFetch("never");
    render(withIntl(<Harness onOpenPicker={() => {}} />));

    // No await on the read: the control is usable immediately.
    fireEvent.click(screen.getByTestId("open"));
    expect(screen.getByTestId(`explainer-${ID}`)).toBeInTheDocument();
  });

  it("shows the explainer when the backend omits dismissed_explainers entirely", async () => {
    mockSettingsFetch({ ok: true, body: settingsBody(undefined) });
    render(withIntl(<Harness onOpenPicker={() => {}} />));
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    await act(async () => {});

    fireEvent.click(screen.getByTestId("open"));
    expect(screen.getByTestId(`explainer-${ID}`)).toBeInTheDocument();
  });

  it("PATCHes dismiss_explainer ONLY when the checkbox was ticked", async () => {
    const { patches } = mockSettingsFetch({ ok: true, body: settingsBody([]) });
    render(withIntl(<Harness onOpenPicker={() => {}} />));
    await waitFor(() => expect(fetch).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId("open"));
    fireEvent.click(screen.getByTestId(`explainer-${ID}-continue`));
    await act(async () => {});
    expect(patches).toEqual([]);

    fireEvent.click(screen.getByTestId("open"));
    fireEvent.click(screen.getByTestId(`explainer-${ID}-dontshowagain-input`));
    fireEvent.click(screen.getByTestId(`explainer-${ID}-continue`));
    await waitFor(() => expect(patches).toEqual([{ dismiss_explainer: ID }]));
  });

  it("a failed dismissal PATCH is retried once and never surfaces", async () => {
    const patches: unknown[] = [];
    let attempts = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const method = init?.method ?? "GET";
        if (method === "PATCH") {
          attempts += 1;
          patches.push(JSON.parse(init!.body as string));
          if (attempts === 1) throw new Error("network error");
          return { ok: true, status: 200, json: async () => settingsBody([ID]) } as Response;
        }
        return { ok: true, json: async () => settingsBody([]) } as Response;
      }),
    );
    render(withIntl(<Harness onOpenPicker={() => {}} />));
    await waitFor(() => expect(fetch).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId("open"));
    fireEvent.click(screen.getByTestId(`explainer-${ID}-dontshowagain-input`));
    fireEvent.click(screen.getByTestId(`explainer-${ID}-continue`));

    await waitFor(() => expect(attempts).toBe(2));
    expect(patches).toEqual([{ dismiss_explainer: ID }, { dismiss_explainer: ID }]);
    // The explainer closed regardless — a failed write is never a user error.
    expect(screen.queryByTestId(`explainer-${ID}`)).toBeNull();
  });
});
