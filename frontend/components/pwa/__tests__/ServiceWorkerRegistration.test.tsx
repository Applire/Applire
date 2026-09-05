// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ServiceWorkerRegistration } from "../ServiceWorkerRegistration";

// US229 (E040, ADR-050 amendment clause 4d). No Playwright tier reaches this
// component — the OQ lane runs the dev image, where registration is off by
// design — so the gate is here.
function withServiceWorker() {
  const register = vi.fn().mockResolvedValue({});
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: { register },
  });
  return register;
}

afterEach(() => {
  vi.unstubAllEnvs();
  Reflect.deleteProperty(navigator, "serviceWorker");
});

describe("ServiceWorkerRegistration", () => {
  it("registers /sw.js at root scope in a production build", () => {
    vi.stubEnv("NODE_ENV", "production");
    const register = withServiceWorker();
    render(<ServiceWorkerRegistration />);
    expect(register).toHaveBeenCalledWith("/sw.js", { scope: "/" });
  });

  it("does NOT register in a development build", () => {
    vi.stubEnv("NODE_ENV", "development");
    const register = withServiceWorker();
    render(<ServiceWorkerRegistration />);
    expect(register).not.toHaveBeenCalled();
  });

  it("renders nothing", () => {
    vi.stubEnv("NODE_ENV", "production");
    withServiceWorker();
    const { container } = render(<ServiceWorkerRegistration />);
    expect(container).toBeEmptyDOMElement();
  });

  it("survives a browser without service-worker support", () => {
    vi.stubEnv("NODE_ENV", "production");
    Reflect.deleteProperty(navigator, "serviceWorker");
    expect(() => render(<ServiceWorkerRegistration />)).not.toThrow();
  });

  it("swallows a rejected registration instead of surfacing it", async () => {
    vi.stubEnv("NODE_ENV", "production");
    const register = vi.fn().mockRejectedValue(new Error("insecure context"));
    Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: { register } });
    render(<ServiceWorkerRegistration />);
    await expect(Promise.resolve()).resolves.toBeUndefined();
    expect(register).toHaveBeenCalled();
  });
});
