"use client";

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

/* eslint-disable formatjs/no-literal-string-in-jsx -- demo content for color-scheme preview */

export function ThemePreview() {
  return (
    <div className="flex-1 bg-white rounded-xl border border-gray-200 overflow-hidden">
      <div className="px-4 py-3 border-b border-gray-200 text-xs font-semibold text-gray-500 uppercase tracking-wide">
        Live Preview
      </div>
      <div className="p-4 flex flex-col gap-3 min-h-[400px]" style={{ background: "var(--color-surface-dim)" }}>

        {/* Nav bar */}
        <div className="rounded-lg px-4 py-2.5 flex items-center justify-between" style={{ background: "var(--color-primary)" }}>
          <span className="text-white font-bold text-sm">Applire</span>
          <div className="flex gap-4">
            <span className="text-white/70 text-xs">Dashboard</span>
            <span className="text-white/70 text-xs">Profile</span>
          </div>
        </div>

        {/* Application card */}
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <div className="font-semibold text-sm mb-1" style={{ color: "var(--color-neutral-dark, #2C3E50)" }}>Software Engineer</div>
          <div className="text-xs text-gray-500 mb-3">Tailored for Acme GmbH · 3 gaps identified</div>
          <div className="flex gap-2 items-center">
            <span className="text-white text-xs px-2 py-1 rounded font-semibold" style={{ background: "var(--color-teal)" }}>View CV</span>
            <span className="text-xs px-2 py-1 rounded font-semibold" style={{ background: "var(--color-primary-container)", color: "var(--color-primary)" }}>Interview</span>
            <span className="ml-auto text-xs px-2 py-1 rounded font-semibold" style={{ background: "var(--color-gold-container)", color: "var(--color-gold-dim)" }}>3 gaps</span>
          </div>
        </div>

        {/* Button row */}
        <div className="flex gap-2 flex-wrap">
          <button className="text-white text-xs px-3 py-1.5 rounded-md font-semibold" style={{ background: "var(--color-primary)" }}>Primary</button>
          <button className="text-white text-xs px-3 py-1.5 rounded-md font-semibold" style={{ background: "var(--color-teal)" }}>Accent</button>
          <button className="text-xs px-3 py-1.5 rounded-md font-semibold border" style={{ borderColor: "var(--color-teal)", color: "var(--color-teal)" }}>Outline</button>
          <button className="text-xs px-3 py-1.5 rounded-md font-semibold" style={{ background: "var(--color-primary-container)", color: "var(--color-primary)" }}>Subtle</button>
        </div>

        {/* Form input */}
        <div>
          <label className="block text-xs font-semibold mb-1" style={{ color: "var(--color-neutral-dark, #2C3E50)" }}>Job title</label>
          <input
            readOnly
            value="Senior Software Engineer"
            className="w-full rounded-md px-3 py-2 text-xs bg-white border"
            style={{ borderColor: "var(--color-teal)", color: "var(--color-neutral-dark, #2C3E50)" }}
          />
        </div>

        {/* Skill badges */}
        <div className="flex gap-2 flex-wrap">
          {["Python", "FastAPI", "Docker", "PostgreSQL"].map((skill) => (
            <span
              key={skill}
              className="text-xs px-2 py-1 rounded-full font-semibold"
              style={{ background: "var(--color-teal-container)", color: "var(--color-primary)" }}
            >
              {skill}
            </span>
          ))}
        </div>

        {/* Link */}
        <a className="text-xs underline cursor-pointer" style={{ color: "var(--color-teal)" }}>← Back to Dashboard</a>
      </div>
    </div>
  );
}
