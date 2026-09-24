// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * GapClusterCard — ADR-089 clause 8 + ruling C-1: the card renders the
 * server's own per-member ledger facts (`member_statuses`) through
 * `clusterView`'s worst-member tone/pill, never client-only click state.
 * `cardTone` itself is unit-tested alongside `clusterView` in
 * `lib/match-utils.ts`'s own test file — this file exercises the CARD's
 * rendering of that record (chips, edge colour, pill), not the tone function.
 */
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { GapClusterCard } from "../GapClusterCard";
import { clusterView, type GapCluster, type TurnClusterCoverage } from "@/lib/match-utils";
import { withIntl } from "@/lib/test-utils/with-intl";

function makeCluster(overrides: Partial<GapCluster> = {}): GapCluster {
  return {
    id: "c1",
    label: "Cloud experience",
    category: "C",
    gaps: [],
    jd_skills: [],
    jd_context: "",
    outcome: { asked: 0, covered: [], declined: [], session_ids: [] },
    coverage: "open",
    budget_remaining: 2,
    member_statuses: [],
    ...overrides,
  };
}

describe("GapClusterCard", () => {
  it("renders an open C cluster as red/askable with no status pill", () => {
    const cluster = makeCluster({
      category: "C",
      gaps: ["Kubernetes"],
      coverage: "open",
      member_statuses: [{ member: "Kubernetes", status: "gap" }],
    });
    const view = clusterView(cluster);
    render(withIntl(<GapClusterCard cluster={cluster} view={view} />));

    const card = screen.getByTestId("gap-cluster-card");
    expect(card).toHaveAttribute("data-coverage", "open");
    expect(card).toHaveAttribute("data-tone", "red");
    expect(card).toHaveAttribute("data-askable", "true");
    expect(card.className).toContain("border-l-critical");
    expect(screen.queryByTestId("gap-resolved")).not.toBeInTheDocument();
    expect(screen.queryByTestId("gap-partly-covered")).not.toBeInTheDocument();
    expect(screen.queryByTestId("gap-likely-match")).not.toBeInTheDocument();
    expect(screen.queryByTestId("gap-declined")).not.toBeInTheDocument();
  });

  it("renders an open B cluster as yellow — an open member defaults to partial, not gap, in category B", () => {
    const cluster = makeCluster({
      category: "B",
      gaps: ["Cross-cultural communication"],
      coverage: "open",
      member_statuses: [{ member: "Cross-cultural communication", status: "partial" }],
    });
    const view = clusterView(cluster);
    render(withIntl(<GapClusterCard cluster={cluster} view={view} />));

    const card = screen.getByTestId("gap-cluster-card");
    expect(card).toHaveAttribute("data-tone", "yellow");
    expect(card.className).toContain("border-l-warning");
    expect(screen.queryByTestId("gap-resolved")).not.toBeInTheDocument();
  });

  it("renders a member chip per ledger fact — covered/partial/gap/declined each with their own data-state and background", () => {
    const cluster = makeCluster({
      gaps: ["Terraform", "Ansible"],
      outcome: { asked: 2, covered: ["Docker"], declined: ["Scala"], session_ids: ["s1"] },
      coverage: "partly_covered",
      budget_remaining: 1,
      member_statuses: [
        { member: "Docker", status: "covered" },
        { member: "Terraform", status: "gap" },
        { member: "Ansible", status: "partial" },
        { member: "Scala", status: "declined" },
      ],
    });
    const view = clusterView(cluster);
    render(withIntl(<GapClusterCard cluster={cluster} view={view} />));

    const chips = screen.getAllByTestId("gap-member");
    const byTerm = (term: string) => chips.find((c) => c.textContent?.includes(term));

    expect(byTerm("Docker")).toHaveAttribute("data-state", "covered");
    expect(byTerm("Docker")?.className).toContain("bg-success-container");
    expect(byTerm("Terraform")).toHaveAttribute("data-state", "gap");
    expect(byTerm("Terraform")?.className).toContain("bg-critical-container");
    expect(byTerm("Ansible")).toHaveAttribute("data-state", "partial");
    expect(byTerm("Ansible")?.className).toContain("bg-warning-container");
    expect(byTerm("Scala")).toHaveAttribute("data-state", "declined");
    expect(byTerm("Scala")?.className).toContain("bg-surface-container");
  });

  describe("card tone — the WORST non-declined, non-closed member (ruling C-1)", () => {
    it("3 covered + 1 partial → yellow", () => {
      const cluster = makeCluster({
        gaps: ["Ansible"],
        outcome: { asked: 1, covered: ["Docker", "Kubernetes", "Terraform"], declined: [], session_ids: ["s1"] },
        coverage: "partly_covered",
        budget_remaining: 1,
        member_statuses: [
          { member: "Docker", status: "covered" },
          { member: "Kubernetes", status: "covered" },
          { member: "Terraform", status: "covered" },
          { member: "Ansible", status: "partial" },
        ],
      });
      const view = clusterView(cluster);
      render(withIntl(<GapClusterCard cluster={cluster} view={view} />));

      const card = screen.getByTestId("gap-cluster-card");
      expect(card).toHaveAttribute("data-tone", "yellow");
      expect(card.className).toContain("border-l-warning");
    });

    it("covered + partial + gap → red", () => {
      const cluster = makeCluster({
        gaps: ["Ansible", "Helm"],
        outcome: { asked: 1, covered: ["Docker"], declined: [], session_ids: ["s1"] },
        coverage: "partly_covered",
        budget_remaining: 1,
        member_statuses: [
          { member: "Docker", status: "covered" },
          { member: "Ansible", status: "partial" },
          { member: "Helm", status: "gap" },
        ],
      });
      const view = clusterView(cluster);
      render(withIntl(<GapClusterCard cluster={cluster} view={view} />));

      const card = screen.getByTestId("gap-cluster-card");
      expect(card).toHaveAttribute("data-tone", "red");
      expect(card.className).toContain("border-l-critical");
    });

    it("5 covered → green", () => {
      const cluster = makeCluster({
        gaps: [],
        outcome: {
          asked: 1,
          covered: ["Docker", "Kubernetes", "Terraform", "Helm", "Ansible"],
          declined: [],
          session_ids: ["s1"],
        },
        coverage: "covered",
        budget_remaining: 1,
        member_statuses: [
          { member: "Docker", status: "covered" },
          { member: "Kubernetes", status: "covered" },
          { member: "Terraform", status: "covered" },
          { member: "Helm", status: "covered" },
          { member: "Ansible", status: "covered" },
        ],
      });
      const view = clusterView(cluster);
      render(withIntl(<GapClusterCard cluster={cluster} view={view} />));

      const card = screen.getByTestId("gap-cluster-card");
      expect(card).toHaveAttribute("data-tone", "green");
      expect(card.className).toContain("border-l-success");
    });

    it("a declined member never colours a card that has other (live) members — covered + declined stays green, not grey", () => {
      const cluster = makeCluster({
        gaps: [],
        outcome: { asked: 1, covered: ["Docker"], declined: ["Cobol"], session_ids: ["s1"] },
        coverage: "partly_covered",
        budget_remaining: 1,
        member_statuses: [
          { member: "Docker", status: "covered" },
          { member: "Cobol", status: "declined" },
        ],
      });
      const view = clusterView(cluster);
      render(withIntl(<GapClusterCard cluster={cluster} view={view} />));

      const card = screen.getByTestId("gap-cluster-card");
      expect(card).toHaveAttribute("data-tone", "green");
      expect(card.className).toContain("border-l-success");
    });
  });

  describe("status pill — kind, wording, and the C-1b 'likely match' exception", () => {
    it("all covered → gap-resolved 'Covered', pill tone matches the card (green)", () => {
      const cluster = makeCluster({
        gaps: [],
        outcome: { asked: 1, covered: ["Docker"], declined: [], session_ids: ["s1"] },
        coverage: "covered",
        budget_remaining: 1,
        member_statuses: [{ member: "Docker", status: "covered" }],
      });
      const view = clusterView(cluster);
      render(withIntl(<GapClusterCard cluster={cluster} view={view} />));

      const pill = screen.getByTestId("gap-resolved");
      expect(pill).toHaveTextContent("Covered");
      expect(pill).toHaveAttribute("data-tone", "green");
    });

    it("partly_covered with an already-asked question → gap-partly-covered 'Partly covered'", () => {
      const cluster = makeCluster({
        gaps: ["Ansible"],
        outcome: { asked: 1, covered: ["Docker"], declined: [], session_ids: ["s1"] },
        coverage: "partly_covered",
        budget_remaining: 1,
        member_statuses: [
          { member: "Docker", status: "covered" },
          { member: "Ansible", status: "partial" },
        ],
      });
      const view = clusterView(cluster);
      render(withIntl(<GapClusterCard cluster={cluster} view={view} />));

      const pill = screen.getByTestId("gap-partly-covered");
      expect(pill).toHaveTextContent("Partly covered");
      expect(pill).toHaveAttribute("data-tone", "yellow");
      expect(screen.queryByTestId("gap-likely-match")).not.toBeInTheDocument();
    });

    it("declined → gap-declined, and an all-declined cluster reads grey", () => {
      const cluster = makeCluster({
        gaps: [],
        outcome: { asked: 1, covered: [], declined: ["Cobol"], session_ids: ["s1"] },
        coverage: "declined",
        budget_remaining: 1,
        member_statuses: [{ member: "Cobol", status: "declined" }],
      });
      const view = clusterView(cluster);
      render(withIntl(<GapClusterCard cluster={cluster} view={view} />));

      const pill = screen.getByTestId("gap-declined");
      expect(pill).toHaveAttribute("data-tone", "grey");
      expect(screen.getByTestId("gap-cluster-card").className).toContain("border-l-outline-variant");
    });

    it("C-1b: partly_covered, nobody asked yet, and the tone is yellow → 'Likely match' instead of 'Partly covered'", () => {
      const cluster = makeCluster({
        gaps: ["Ansible"],
        outcome: { asked: 0, covered: [], declined: [], session_ids: [] },
        coverage: "partly_covered",
        budget_remaining: 2,
        member_statuses: [{ member: "Ansible", status: "partial" }],
      });
      const view = clusterView(cluster);
      render(withIntl(<GapClusterCard cluster={cluster} view={view} />));

      const pill = screen.getByTestId("gap-likely-match");
      expect(pill).toHaveTextContent("Likely match");
      expect(pill).toHaveAttribute("data-tone", "yellow");
      expect(screen.queryByTestId("gap-partly-covered")).not.toBeInTheDocument();
    });

    it("C-1b does NOT fire when nobody asked yet but the tone is red (a real gap alongside the partial)", () => {
      const cluster = makeCluster({
        gaps: ["Ansible", "Helm"],
        outcome: { asked: 0, covered: [], declined: [], session_ids: [] },
        coverage: "partly_covered",
        budget_remaining: 2,
        member_statuses: [
          { member: "Ansible", status: "partial" },
          { member: "Helm", status: "gap" },
        ],
      });
      const view = clusterView(cluster);
      render(withIntl(<GapClusterCard cluster={cluster} view={view} />));

      expect(screen.getByTestId("gap-partly-covered")).toHaveTextContent("Partly covered");
      expect(screen.queryByTestId("gap-likely-match")).not.toBeInTheDocument();
    });
  });

  it("shows the budget-spent line naming the asked count and is not askable", () => {
    const cluster = makeCluster({
      gaps: ["Terraform"],
      outcome: { asked: 2, covered: [], declined: [], session_ids: ["s1", "s2"] },
      coverage: "partly_covered",
      budget_remaining: 0,
      member_statuses: [{ member: "Terraform", status: "gap" }],
    });
    const view = clusterView(cluster);
    render(withIntl(<GapClusterCard cluster={cluster} view={view} />));

    expect(screen.getByTestId("gap-budget-spent")).toHaveTextContent("2 questions");
    expect(screen.getByTestId("gap-cluster-card")).toHaveAttribute("data-askable", "false");
  });

  describe("clickability", () => {
    it("adds cursor-pointer and calls onClick when a handler is supplied", () => {
      const cluster = makeCluster();
      const view = clusterView(cluster);
      const onClick = vi.fn();
      render(withIntl(<GapClusterCard cluster={cluster} view={view} onClick={onClick} />));

      const card = screen.getByTestId("gap-cluster-card");
      expect(card.className).toContain("cursor-pointer");
      fireEvent.click(card);
      expect(onClick).toHaveBeenCalledTimes(1);
    });

    it("has no cursor-pointer without an onClick handler", () => {
      const cluster = makeCluster();
      const view = clusterView(cluster);
      render(withIntl(<GapClusterCard cluster={cluster} view={view} />));

      expect(screen.getByTestId("gap-cluster-card").className).not.toContain("cursor-pointer");
    });

    it("adds opacity-60 when locked", () => {
      const cluster = makeCluster();
      const view = clusterView(cluster);
      render(withIntl(<GapClusterCard cluster={cluster} view={view} locked />));

      expect(screen.getByTestId("gap-cluster-card").className).toContain("opacity-60");
    });
  });

  it("overlays a turn's open_concepts onto member state — a member that left the open list but wasn't re-read yet shows closed, never a false covered", () => {
    const cluster = makeCluster({
      category: "C",
      gaps: ["Kubernetes", "Terraform"],
      outcome: { asked: 0, covered: [], declined: [], session_ids: [] },
      coverage: "open",
      budget_remaining: 2,
      member_statuses: [],
    });
    const turn: TurnClusterCoverage = {
      cluster_id: cluster.id,
      coverage: "partly_covered",
      open_concepts: ["Terraform"],
      budget_remaining: 1,
    };
    const view = clusterView(cluster, turn);
    render(withIntl(<GapClusterCard cluster={cluster} view={view} />));

    const chips = screen.getAllByTestId("gap-member");
    const kubernetes = chips.find((c) => c.textContent?.includes("Kubernetes"));
    const terraform = chips.find((c) => c.textContent?.includes("Terraform"));
    // Kubernetes left the open list on this turn — the page hasn't re-read
    // whether it was covered or declined, so it must read "closed", never
    // "covered" (no false green).
    expect(kubernetes).toHaveAttribute("data-state", "closed");
    // Terraform is still open per the turn — category C, no explicit fact,
    // so it reads the open fallback "gap".
    expect(terraform).toHaveAttribute("data-state", "gap");
  });
});
