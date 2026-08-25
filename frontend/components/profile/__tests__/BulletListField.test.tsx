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

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { BulletListField } from "../BulletListField";
import { withIntl } from "@/lib/test-utils/with-intl";

describe("BulletListField", () => {
  it("adds a new empty bullet without confirmation", () => {
    const onChange = vi.fn();
    render(
      withIntl(
        <BulletListField
          id="bullets"
          label="Achievements"
          items={["Shipped X"]}
          onChange={onChange}
          addButtonLabel="Add"
          itemAriaLabel={(i) => `Item ${i + 1}`}
        />,
      ),
    );

    fireEvent.click(screen.getByTestId("bullets-add"));
    expect(onChange).toHaveBeenCalledWith(["Shipped X", ""]);
  });

  it("edits a bullet inline", () => {
    const onChange = vi.fn();
    render(
      withIntl(
        <BulletListField
          id="bullets"
          label="Achievements"
          items={["Shipped X"]}
          onChange={onChange}
          addButtonLabel="Add"
          itemAriaLabel={(i) => `Item ${i + 1}`}
        />,
      ),
    );

    fireEvent.change(screen.getByTestId("bullets-item-0"), { target: { value: "Shipped Y" } });
    expect(onChange).toHaveBeenCalledWith(["Shipped Y"]);
  });

  // H1.7 — removing a bullet needs no confirmation dialog.
  it("removes a bullet immediately, no confirmation", () => {
    const onChange = vi.fn();
    render(
      withIntl(
        <BulletListField
          id="bullets"
          label="Achievements"
          items={["A", "B"]}
          onChange={onChange}
          addButtonLabel="Add"
          itemAriaLabel={(i) => `Item ${i + 1}`}
        />,
      ),
    );

    fireEvent.click(screen.getByTestId("bullets-remove-0"));
    expect(onChange).toHaveBeenCalledWith(["B"]);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  // H1.7 — move up/down reorders bullets (content order is editable).
  it("moves a bullet down and back up", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      withIntl(
        <BulletListField
          id="bullets"
          label="Achievements"
          items={["A", "B"]}
          onChange={onChange}
          addButtonLabel="Add"
          itemAriaLabel={(i) => `Item ${i + 1}`}
        />,
      ),
    );

    fireEvent.click(screen.getByTestId("bullets-down-0"));
    expect(onChange).toHaveBeenLastCalledWith(["B", "A"]);

    rerender(
      withIntl(
        <BulletListField
          id="bullets"
          label="Achievements"
          items={["B", "A"]}
          onChange={onChange}
          addButtonLabel="Add"
          itemAriaLabel={(i) => `Item ${i + 1}`}
        />,
      ),
    );
    fireEvent.click(screen.getByTestId("bullets-up-1"));
    expect(onChange).toHaveBeenLastCalledWith(["A", "B"]);
  });

  it("hides reorder controls when allowReorder is false", () => {
    render(
      withIntl(
        <BulletListField
          id="tags"
          label="Technologies"
          items={["Python"]}
          onChange={vi.fn()}
          addButtonLabel="Add"
          itemAriaLabel={(i) => `Item ${i + 1}`}
          allowReorder={false}
        />,
      ),
    );
    expect(screen.queryByTestId("tags-up-0")).not.toBeInTheDocument();
    expect(screen.queryByTestId("tags-down-0")).not.toBeInTheDocument();
  });
});
