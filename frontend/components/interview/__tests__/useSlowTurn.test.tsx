// Copyright (C) 2026 Tobias Rosenbaum
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

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SLOW_TURN_AFTER_SECONDS, useTurnElapsed } from "../useSlowTurn";

describe("787:205 — useTurnElapsed", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("counts seconds only while a turn runs, and resets when it ends", () => {
    const { result, rerender } = renderHook(({ on }) => useTurnElapsed(on), { initialProps: { on: false } });
    expect(result.current).toBe(0);
    rerender({ on: true });
    act(() => void vi.advanceTimersByTime(SLOW_TURN_AFTER_SECONDS * 1000));
    expect(result.current).toBe(SLOW_TURN_AFTER_SECONDS);
    rerender({ on: false });
    expect(result.current).toBe(0);
    rerender({ on: true });
    expect(result.current).toBe(0);
  });
});
