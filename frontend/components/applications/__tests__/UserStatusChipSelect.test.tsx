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

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { UserStatusChipSelect } from "../UserStatusChipSelect";
import { USER_STATUS_OPTIONS } from "@/lib/user-status";

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}:${Object.values(params).join(",")}` : key,
}));

describe("UserStatusChipSelect", () => {
  it("renders every USER_STATUS_OPTIONS entry", () => {
    render(<UserStatusChipSelect value="tracking" onChange={vi.fn()} />);
    const select = screen.getByRole("combobox", { name: "statusSelectLabel" });
    const values = Array.from(select.querySelectorAll("option")).map((o) =>
      o.getAttribute("value"),
    );
    expect(values).toEqual(USER_STATUS_OPTIONS.map((o) => o.value));
  });

  it("fires onChange with the picked value", () => {
    const onChange = vi.fn();
    render(<UserStatusChipSelect value="tracking" onChange={onChange} />);
    fireEvent.change(screen.getByRole("combobox", { name: "statusSelectLabel" }), {
      target: { value: "interviewing" },
    });
    expect(onChange).toHaveBeenCalledWith("interviewing");
  });

  it("applies the selected option's className", () => {
    render(<UserStatusChipSelect value="hired" onChange={vi.fn()} />);
    const select = screen.getByRole("combobox", { name: "statusSelectLabel" });
    const hiredOption = USER_STATUS_OPTIONS.find((o) => o.value === "hired")!;
    for (const cls of hiredOption.className.split(" ")) {
      expect(select).toHaveClass(cls);
    }
  });

  it("stops click propagation when stopClickPropagation is set", () => {
    const onCardClick = vi.fn();
    render(
      <div onClick={onCardClick}>
        <UserStatusChipSelect value="tracking" onChange={vi.fn()} stopClickPropagation />
      </div>,
    );
    fireEvent.click(screen.getByRole("combobox", { name: "statusSelectLabel" }));
    expect(onCardClick).not.toHaveBeenCalled();
  });

  it("does not stop click propagation by default", () => {
    const onCardClick = vi.fn();
    render(
      <div onClick={onCardClick}>
        <UserStatusChipSelect value="tracking" onChange={vi.fn()} />
      </div>,
    );
    fireEvent.click(screen.getByRole("combobox", { name: "statusSelectLabel" }));
    expect(onCardClick).toHaveBeenCalled();
  });
});
