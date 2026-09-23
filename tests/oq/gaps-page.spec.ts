// tests/e2e/oq/gaps-page.spec.ts
import { test, expect } from "@playwright/test";

/**
 * Gaps Page — OQ Tests
 *
 * Covers:
 *  - Gap categories render with correct severity dot colors
 *    (Cat B = warning, Cat C = critical — Material-3 theme tokens)
 *  - ADR-089 clause 8: card state from the persisted coverage record (four
 *    states + a spent budget) and across a reload; the answer turn's follow-up
 *    shown inline in the same card; completion replaces the WHOLE analysis;
 *    one open micro-session per page; a 409 refusal said inline
 *  - "Generate CV Now" button calls advance API and navigates to CV page
 *  - "Quick Interview" button visible for new users with gaps, navigates to interview page
 *  - Error state renders when advance API fails
 *
 * Uses page.route() mocks — does NOT require a running backend.
 */

const FLOW_ID = "flow-test-0000-0000-0000-000000000001";
const JOB_ID = "job-test-0000-0000-0000-000000000002";
const GAP_ID = "gap-test-0000-0000-0000-000000000003";

const MOCK_FLOW_STATE = {
  job_id: JOB_ID,
  user_type: "new",
  available_actions: {},
  gap_summary: { gap_analysis_id: GAP_ID },
  job_summary: { role_title: "Senior Software Engineer" },
};

const CLUSTER_ID_C = "cluster-test-c-0000-0000-0000-000000000005";
const CLUSTER_ID_B = "cluster-test-b-0000-0000-0000-000000000006";

const MOCK_GAP_ANALYSIS = {
  id: GAP_ID,
  match_score: 0.72,
  category_a: ["Python", "FastAPI"],
  category_b: ["Docker", "PostgreSQL"],
  category_c: ["Kubernetes", "Terraform"],
  strengths: ["Python", "FastAPI"],
  gap_clusters: [
    {
      id: CLUSTER_ID_C,
      label: "Container Orchestration",
      category: "C",
      gaps: ["Kubernetes", "Terraform"],
      jd_skills: ["Kubernetes"],
      jd_context: "Required for production deployments",
      outcome: { asked: 0, covered: [], declined: [], session_ids: [] },
      coverage: "open",
      budget_remaining: 2,
    },
    {
      id: CLUSTER_ID_B,
      label: "Database Operations",
      category: "B",
      gaps: ["Docker", "PostgreSQL"],
      jd_skills: ["Docker"],
      jd_context: "Nice to have for containerised deployments",
      outcome: { asked: 0, covered: [], declined: [], session_ids: [] },
      coverage: "open",
      budget_remaining: 2,
    },
  ],
};

const MOCK_PROFILE = {
  positions_count: 5,
  projects_count: 12,
  certifications_count: 3,
  data_points_count: 47,
};

const MOCK_ADVANCE_RESPONSE = { status: "ok" };

async function setupGapsPageMocks(page: import("@playwright/test").Page) {
  await page.route(`**/api/flow/${FLOW_ID}/state`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_FLOW_STATE) })
  );
  await page.route(`**/api/job/${JOB_ID}/gaps`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_GAP_ANALYSIS) })
  );
  await page.route(`**/api/profile`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_PROFILE) })
  );
  // Frontend collector #677: without this route the spec's UI LANGUAGE came
  // from whatever backend `next.config.ts` proxied `/api/*` to — the English
  // assertions below passed in CI only because the CI backend happened to
  // answer `en`. `LocaleProvider` reads it from here, so the locale is now the
  // spec's own statement rather than the environment's. (The mobile twin,
  // tests/oq/mobile/gaps-triage.spec.ts, already carried this mock.)
  await page.route("**/api/settings", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ui_language: "en", dismissed_explainers: [] }),
    })
  );
}

test.describe("Gaps page", () => {
  test("renders gap categories with correct severity dot colors", async ({ page }) => {
    await setupGapsPageMocks(page);
    await page.goto(`/flow/${FLOW_ID}/gaps`);
    await expect(page.getByTestId("gap-analysis-page")).toBeVisible({ timeout: 10000 });

    // Cluster cards must be visible
    const cards = page.getByTestId("gap-cluster-card");
    await expect(cards.first()).toBeVisible();

    // Category C cluster card — dot must use the critical token (not warning)
    const catCCard = page.getByTestId("gap-cluster-card").filter({ hasText: "Container Orchestration" });
    await expect(catCCard).toBeVisible();
    const catCDot = catCCard.locator("span.rounded-full").first();
    const catCClass = await catCDot.getAttribute("class");
    expect(catCClass).toContain("bg-critical");
    expect(catCClass).not.toContain("bg-warning");

    // Category B cluster card — dot must use the warning token (not critical)
    const catBCard = page.getByTestId("gap-cluster-card").filter({ hasText: "Database Operations" });
    await expect(catBCard).toBeVisible();
    const catBDot = catBCard.locator("span.rounded-full").first();
    const catBClass = await catBDot.getAttribute("class");
    expect(catBClass).toContain("bg-warning");
    expect(catBClass).not.toContain("bg-critical");

    // The tokens actually paint (an undefined utility no-ops silently): the
    // computed colours are the theme's critical #D94F4F / warning #E5A832.
    expect(await catCDot.evaluate((el) => getComputedStyle(el).backgroundColor)).toBe("rgb(217, 79, 79)");
    expect(await catBDot.evaluate((el) => getComputedStyle(el).backgroundColor)).toBe("rgb(229, 168, 50)");
  });

  test("shows correct gap counts in badges", async ({ page }) => {
    await setupGapsPageMocks(page);
    await page.goto(`/flow/${FLOW_ID}/gaps`);
    await expect(page.getByTestId("gap-analysis-page")).toBeVisible({ timeout: 10000 });

    // Match score display
    await expect(page.getByTestId("match-score-display")).toContainText("72%");

    // Gaps section shows the canonical gap count = number of gap clusters (E037 F1:
    // the heading matches the badge). The 4 Cat B+C skills collapse into 2 clusters.
    await expect(page.getByTestId("gaps-section")).toBeVisible();
    await expect(page.getByTestId("gaps-section")).toContainText("2 gaps identified");
  });

  test("Generate CV Now button advances flow and navigates to CV page", async ({ page }) => {
    await setupGapsPageMocks(page);

    let advanceCalled = false;
    await page.route(`**/api/flow/${FLOW_ID}/advance`, (route) => {
      advanceCalled = true;
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_ADVANCE_RESPONSE) });
    });

    await page.goto(`/flow/${FLOW_ID}/gaps`);
    await expect(page.getByTestId("gap-analysis-page")).toBeVisible({ timeout: 10000 });

    await page.getByTestId("generate-cv-button").click();

    await expect(page).toHaveURL(`/flow/${FLOW_ID}/cv`, { timeout: 10000 });
    expect(advanceCalled).toBe(true);
  });

  test("Quick Interview button visible for new user with gaps and navigates to interview", async ({ page }) => {
    await setupGapsPageMocks(page);

    const SESSION_ID = "session-test-0000-0000-0000-000000000004";
    await page.route(`**/api/session`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ session_id: SESSION_ID, id: SESSION_ID }),
      })
    );
    await page.route(`**/api/flow/${FLOW_ID}/advance`, (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_ADVANCE_RESPONSE) })
    );

    await page.goto(`/flow/${FLOW_ID}/gaps`);
    await expect(page.getByTestId("gap-analysis-page")).toBeVisible({ timeout: 10000 });

    const interviewButton = page.getByTestId("interview-button");
    await expect(interviewButton).toBeVisible();
    await interviewButton.click();

    await expect(page).toHaveURL(`/flow/${FLOW_ID}/interview`, { timeout: 10000 });
  });

  test("Quick Interview offered to RETURNING user with gaps; onboarding hero hidden (ADR-016 amended)", async ({ page }) => {
    // Spaghettieis UAT: the follow-up flow (returning + job) hid both the gap
    // mitigation path and must not open with the onboarding "Master Profile
    // Created" summary.
    await page.route(`**/api/flow/${FLOW_ID}/state`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...MOCK_FLOW_STATE, user_type: "returning", created_at: "2026-07-13T10:00:00Z" }),
      })
    );
    await page.route(`**/api/job/${JOB_ID}/gaps`, (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_GAP_ANALYSIS) })
    );
    await page.route(`**/api/profile`, (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_PROFILE) })
    );

    await page.goto(`/flow/${FLOW_ID}/gaps`);
    await expect(page.getByTestId("gap-analysis-page")).toBeVisible({ timeout: 10000 });

    await expect(page.getByTestId("interview-button")).toBeVisible();
    await expect(page.getByTestId("generate-cv-button")).toBeVisible();
    // No onboarding chrome on a JD-only follow-up.
    await expect(page.getByText(/Master Profile Created|Master-Profil erstellt/)).toHaveCount(0);
  });

  test("clicking a gap cluster card starts the micro-session inline", async ({ page }) => {
    await setupGapsPageMocks(page);

    const QUESTION = "Beschreibe deine Erfahrung mit Kubernetes.";
    await page.route(`**/api/session`, (route) => {
      const body = route.request().postDataJSON();
      if (body?.mode === "targeted") {
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            session_id: "micro-session-001",
            first_question: QUESTION,
            choices: null,
          }),
        });
      } else {
        route.continue();
      }
    });

    await page.goto(`/flow/${FLOW_ID}/gaps`);
    await expect(page.getByTestId("gap-analysis-page")).toBeVisible({ timeout: 10000 });

    // Card should be clickable (cursor-pointer) in idle state
    const card = page.getByTestId("gap-cluster-card").first();
    await expect(card).toBeVisible();
    await card.click();

    // Question panel should appear inline
    await expect(page.getByTestId("gap-question")).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId("gap-question")).toContainText(QUESTION);

    // Card is no longer clickable once session is open
    const classAttr = await card.getAttribute("class");
    expect(classAttr).not.toContain("cursor-pointer");
  });

  test("shows error message when advance API fails", async ({ page }) => {
    await setupGapsPageMocks(page);
    await page.route(`**/api/flow/${FLOW_ID}/advance`, (route) =>
      route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Invalid step transition" }),
      })
    );

    await page.goto(`/flow/${FLOW_ID}/gaps`);
    await expect(page.getByTestId("gap-analysis-page")).toBeVisible({ timeout: 10000 });

    await page.getByTestId("generate-cv-button").click();

    await expect(page.getByTestId("error-message")).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId("error-message")).toContainText("Invalid step transition");
  });
});

// ---------------------------------------------------------------------------
// ADR-089 clause 8 — the gaps page renders server truth
// ---------------------------------------------------------------------------

type Json = Record<string, unknown>;
const outcome = (asked = 0, covered: string[] = [], declined: string[] = []) => ({
  asked,
  covered,
  declined,
  session_ids: asked ? ["session-old"] : [],
});

function recordCluster(id: string, label: string, over: Json = {}): Json {
  return {
    id,
    label,
    category: "C",
    gaps: [],
    jd_skills: [],
    jd_context: "",
    outcome: outcome(),
    coverage: "open",
    budget_remaining: 2,
    ...over,
  };
}

type MemberStatus = "covered" | "partial" | "gap" | "declined";
const facts = (pairs: [string, MemberStatus][]) => pairs.map(([member, status]) => ({ member, status }));

const RECORD_CLUSTERS = [
  recordCluster("cl-open", "Container Orchestration", {
    gaps: ["Kubernetes", "Helm"],
    member_statuses: facts([["Kubernetes", "gap"], ["Helm", "gap"]]),
  }),
  recordCluster("cl-partly", "Infrastructure as Code", {
    gaps: ["Terraform"],
    outcome: outcome(1, ["Ansible"]),
    coverage: "partly_covered",
    budget_remaining: 1,
    member_statuses: facts([["Terraform", "partial"], ["Ansible", "covered"]]),
  }),
  recordCluster("cl-spent", "Observability", {
    gaps: ["Prometheus"],
    outcome: outcome(2, ["Grafana"]),
    coverage: "partly_covered",
    budget_remaining: 0,
    member_statuses: facts([["Prometheus", "gap"], ["Grafana", "covered"]]),
  }),
  recordCluster("cl-covered", "CI/CD Pipelines", {
    outcome: outcome(1, ["GitHub Actions", "Jenkins"]),
    coverage: "covered",
    budget_remaining: 1,
    member_statuses: facts([["GitHub Actions", "covered"], ["Jenkins", "covered"]]),
  }),
  recordCluster("cl-declined", "Go Development", {
    outcome: outcome(1, [], ["Go"]),
    coverage: "declined",
    budget_remaining: 1,
    member_statuses: facts([["Go", "declined"]]),
  }),
];

function recordAnalysis(clusters: Json[], over: Json = {}): Json {
  return {
    ...MOCK_GAP_ANALYSIS,
    category_a: ["Python", "Ansible", "Grafana", "GitHub Actions", "Jenkins"],
    category_b: [],
    category_c: ["Kubernetes", "Helm", "Terraform", "Prometheus", "Go"],
    gap_clusters: clusters,
    ...over,
  };
}

/** Mocks a stateful backend: the GET returns whatever `state.row` is now. */
async function setupRecordMocks(page: import("@playwright/test").Page, state: { row: Json }) {
  await setupGapsPageMocks(page);
  await page.route(`**/api/job/${JOB_ID}/gaps`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(state.row) })
  );
  await page.route("**/api/profile/health", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ issues: [] }) })
  );
}

const cardById = (page: import("@playwright/test").Page, id: string) =>
  page.locator(`[data-testid="gap-cluster-card"][data-cluster-id="${id}"]`);

test.describe("Gaps page — ADR-089 coverage record", () => {
  test("four card states and a spent budget render from the record, and survive a reload", async ({ page }) => {
    const state = { row: recordAnalysis(RECORD_CLUSTERS) };
    await setupRecordMocks(page, state);
    await page.goto(`/flow/${FLOW_ID}/gaps`);
    await expect(page.getByTestId("gap-analysis-page")).toBeVisible({ timeout: 10000 });

    for (let pass = 0; pass < 2; pass++) {
      await expect(cardById(page, "cl-open")).toHaveAttribute("data-coverage", "open");
      // Ruling C-1: the card's colour is its worst requirement.
      await expect(cardById(page, "cl-open")).toHaveAttribute("data-tone", "red");
      await expect(cardById(page, "cl-partly")).toHaveAttribute("data-tone", "yellow");
      await expect(cardById(page, "cl-spent")).toHaveAttribute("data-tone", "red");
      await expect(cardById(page, "cl-covered")).toHaveAttribute("data-tone", "green");
      await expect(cardById(page, "cl-declined")).toHaveAttribute("data-tone", "grey");
      await expect(cardById(page, "cl-partly").getByTestId("gap-partly-covered")).toHaveText("Partly covered");
      await expect(cardById(page, "cl-covered").getByTestId("gap-resolved")).toContainText("Covered");
      await expect(cardById(page, "cl-declined").getByTestId("gap-declined")).toHaveText("You said you don't have this");
      await expect(cardById(page, "cl-spent").getByTestId("gap-budget-spent")).toContainText(
        "No more questions on this gap — you answered 2 questions on it."
      );
      // Clickable exactly when askable.
      await expect(cardById(page, "cl-open")).toHaveClass(/cursor-pointer/);
      await expect(cardById(page, "cl-partly")).toHaveClass(/cursor-pointer/);
      for (const id of ["cl-spent", "cl-covered", "cl-declined"]) {
        await expect(cardById(page, id)).not.toHaveClass(/cursor-pointer/);
      }
      // Covered badge from the record.
      await expect(page.getByText("1 gap covered ✓")).toBeVisible();
      if (pass === 0) await page.reload();
    }
  });

  test("an answer that only partly covers the gap gets its follow-up inline, in the same card; other cards are locked", async ({ page }) => {
    const state = { row: recordAnalysis(RECORD_CLUSTERS) };
    await setupRecordMocks(page, state);
    await page.route("**/api/session", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ session_id: "micro-fu-1", question: "How did you run containers?", choices: null }),
      })
    );
    let refreshed = 0;
    await page.route(`**/api/job/${JOB_ID}/gaps/refresh`, (route) => {
      refreshed += 1;
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(state.row) });
    });
    await page.route("**/api/session/micro-fu-1/message", (route) => {
      // The turn writes its record onto the row (same transaction, server-side).
      state.row = recordAnalysis(
        RECORD_CLUSTERS.map((c) =>
          c.id === "cl-open"
            ? {
                ...c,
                gaps: ["Helm"],
                outcome: outcome(1, ["Kubernetes"]),
                coverage: "partly_covered",
                budget_remaining: 1,
                member_statuses: facts([["Helm", "gap"], ["Kubernetes", "covered"]]),
              }
            : c
        )
      );
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          complete: false,
          question: "And Helm — did you write your own charts?",
          choices: ["Wrote our own charts", "Adapted existing charts"],
          changes_applied: true,
          cluster_coverage: { cluster_id: "cl-open", coverage: "partly_covered", open_concepts: ["Helm"], budget_remaining: 1 },
        }),
      });
    });

    await page.goto(`/flow/${FLOW_ID}/gaps`);
    const open = cardById(page, "cl-open");
    await open.click();
    await expect(open.getByTestId("gap-question")).toHaveText("How did you run containers?");
    // Clause 8 lock: every other card is not clickable while this one is open.
    await expect(page.getByTestId("gaps-locked-hint")).toBeVisible();
    await expect(cardById(page, "cl-partly")).not.toHaveClass(/cursor-pointer/);

    await open.getByTestId("gap-answer-textarea").fill("I ran a 40-service Kubernetes cluster for three years.");
    await open.getByTestId("gap-submit-button").click();

    await expect(open.getByTestId("gap-question")).toHaveText("And Helm — did you write your own charts?");
    await expect(open.getByTestId("gap-follow-up-label")).toHaveText("Follow-up — still open: Helm");
    await expect(open.getByTestId("gap-choice")).toHaveCount(2);
    await expect(open.getByTestId("gap-answer-textarea")).toHaveValue("");
    // Not green: partly covered, Kubernetes covered, Helm open.
    await expect(open).toHaveAttribute("data-coverage", "partly_covered");
    await expect(open.locator('[data-testid="gap-member"][data-state="covered"]')).toContainText("Kubernetes");
    await expect(open.locator('[data-testid="gap-member"][data-state="gap"]')).toContainText("Helm");
    await expect(open).toHaveAttribute("data-tone", "red"); // Helm is still a red gap
    await expect(open.getByTestId("gap-resolved")).toHaveCount(0);
    expect(refreshed).toBe(0);
  });

  test("the turn that completes the micro-session replaces the WHOLE analysis (new list, new score)", async ({ page }) => {
    const state = { row: recordAnalysis(RECORD_CLUSTERS, { match_score: 0.5 }) };
    await setupRecordMocks(page, state);
    await page.route("**/api/session", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ session_id: "micro-done-1", question: "How did you run containers?", choices: null }),
      })
    );
    await page.route("**/api/session/micro-done-1/message", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          complete: true,
          reason: "max_questions_reached",
          cluster_coverage: { cluster_id: "cl-open", coverage: "covered", open_concepts: [], budget_remaining: 1 },
        }),
      })
    );
    const refreshedRow = recordAnalysis(
      [
        ...RECORD_CLUSTERS.map((c) =>
          c.id === "cl-open"
            ? {
                ...c,
                gaps: [],
                outcome: outcome(1, ["Kubernetes", "Helm"]),
                coverage: "covered",
                budget_remaining: 1,
                member_statuses: facts([["Kubernetes", "covered"], ["Helm", "covered"]]),
              }
            : c
        ),
        recordCluster("cl-appended", "Service Mesh", {
          gaps: ["Istio"],
          member_statuses: facts([["Istio", "gap"]]),
        }),
      ],
      { match_score: 0.66 }
    );
    let refreshed = 0;
    await page.route(`**/api/job/${JOB_ID}/gaps/refresh`, (route) => {
      refreshed += 1;
      state.row = refreshedRow;
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(refreshedRow) });
    });

    await page.goto(`/flow/${FLOW_ID}/gaps`);
    await expect(page.getByTestId("match-score-display")).toContainText("50%");
    const open = cardById(page, "cl-open");
    await open.click();
    await open.getByTestId("gap-answer-textarea").fill("Three years of Kubernetes and Helm charts.");
    await open.getByTestId("gap-submit-button").click();

    await expect(open.getByTestId("gap-resolved")).toBeVisible();
    await expect(cardById(page, "cl-appended")).toBeVisible(); // the list was replaced, not only the score
    await expect(page.getByTestId("match-score-display")).toContainText("66%");
    await expect(page.getByText("2 gaps covered ✓")).toBeVisible();
    expect(refreshed).toBe(1);

    // Reload: the same record comes back from the server.
    await page.reload();
    await expect(cardById(page, "cl-open").getByTestId("gap-resolved")).toBeVisible();
    await expect(cardById(page, "cl-appended")).toBeVisible();
  });

  for (const [code, text] of [
    ["gap_budget_spent", "All questions on this gap have been asked already — pick another one."],
    ["gap_already_covered", "This gap is already covered — there is nothing left to ask here."],
  ] as const) {
    test(`a 409 ${code} from POST /api/session is said inline on the card`, async ({ page }) => {
      const state = { row: recordAnalysis(RECORD_CLUSTERS) };
      await setupRecordMocks(page, state);
      await page.route("**/api/session", (route) =>
        route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({ detail: { error_code: code, message: "raw server message" } }),
        })
      );
      await page.goto(`/flow/${FLOW_ID}/gaps`);
      const open = cardById(page, "cl-open");
      await open.click();
      await expect(open.getByTestId("gap-refusal")).toHaveText(text);
      await expect(open.getByTestId("gap-question")).toHaveCount(0);
      await expect(page.getByText("raw server message")).toHaveCount(0);
      await expect(open).not.toHaveClass(/cursor-pointer/);
    });
  }

  test("ruling C-1: each requirement chip has its own colour and the worst one colours the card", async ({ page }) => {
    const state = {
      row: recordAnalysis([
        recordCluster("cl-yellow", "Three green, one yellow", {
          gaps: ["Airflow"],
          outcome: outcome(1, ["Kafka", "Spark", "Flink"]),
          coverage: "partly_covered",
          member_statuses: facts([["Airflow", "partial"], ["Kafka", "covered"], ["Spark", "covered"], ["Flink", "covered"]]),
        }),
        recordCluster("cl-red", "Green, yellow, red", {
          gaps: ["Podman", "Nomad"],
          outcome: outcome(1, ["Docker"]),
          coverage: "partly_covered",
          member_statuses: facts([["Podman", "partial"], ["Nomad", "gap"], ["Docker", "covered"]]),
        }),
        recordCluster("cl-green", "Five green", {
          outcome: outcome(1, ["Git", "Make", "Bash", "Vim", "Jira"]),
          coverage: "covered",
          member_statuses: facts([["Git", "covered"], ["Make", "covered"], ["Bash", "covered"], ["Vim", "covered"], ["Jira", "covered"]]),
        }),
        recordCluster("cl-green-declined", "Green with a declined one", {
          outcome: outcome(1, ["Linux"], ["Windows"]),
          coverage: "covered",
          member_statuses: facts([["Linux", "covered"], ["Windows", "declined"]]),
        }),
      ]),
    };
    await setupRecordMocks(page, state);
    await page.goto(`/flow/${FLOW_ID}/gaps`);

    const edge = (id: string) => cardById(page, id).evaluate((el) => getComputedStyle(el).borderLeftColor);
    await expect(cardById(page, "cl-yellow")).toHaveAttribute("data-tone", "yellow");
    expect(await edge("cl-yellow")).toBe("rgb(229, 168, 50)"); // warning token
    await expect(cardById(page, "cl-red")).toHaveAttribute("data-tone", "red");
    expect(await edge("cl-red")).toBe("rgb(217, 79, 79)"); // critical token
    await expect(cardById(page, "cl-green")).toHaveAttribute("data-tone", "green");
    expect(await edge("cl-green")).toBe("rgb(45, 159, 111)"); // success token
    // Declined members never colour a card that has others.
    await expect(cardById(page, "cl-green-declined")).toHaveAttribute("data-tone", "green");

    // Chips: green = covered, yellow = partial, red = open gap, grey = declined.
    const chip = (id: string, term: string) =>
      cardById(page, id).locator('[data-testid="gap-member"]', { hasText: new RegExp(`^${term}\\(`) });
    await expect(chip("cl-red", "Docker")).toHaveAttribute("data-state", "covered");
    await expect(chip("cl-red", "Podman")).toHaveAttribute("data-state", "partial");
    await expect(chip("cl-red", "Nomad")).toHaveAttribute("data-state", "gap");
    await expect(chip("cl-green-declined", "Windows")).toHaveAttribute("data-state", "declined");
    const bg = (id: string, term: string) => chip(id, term).evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(await bg("cl-red", "Docker")).toBe("rgb(200, 247, 224)"); // success-container
    expect(await bg("cl-red", "Podman")).toBe("rgb(254, 243, 199)"); // warning-container
    expect(await bg("cl-red", "Nomad")).toBe("rgb(253, 232, 232)"); // critical-container

    // The pill colour follows the card: a partly covered card with a red member has a red pill.
    await expect(cardById(page, "cl-red").getByTestId("gap-partly-covered")).toHaveAttribute("data-tone", "red");
  });

  test("ruling C-1b: a likely match nobody has asked yet reads 'Likely match'; after an answer, the normal state", async ({ page }) => {
    const likely = recordCluster("cl-likely", "Databases", {
      category: "B",
      gaps: ["PostgreSQL"],
      coverage: "partly_covered",
      member_statuses: facts([["PostgreSQL", "partial"]]),
    });
    const state = { row: recordAnalysis([likely]) };
    await setupRecordMocks(page, state);
    await page.goto(`/flow/${FLOW_ID}/gaps`);
    await expect(cardById(page, "cl-likely").getByTestId("gap-likely-match")).toHaveText("Likely match");
    await expect(cardById(page, "cl-likely").getByTestId("gap-partly-covered")).toHaveCount(0);

    state.row = recordAnalysis([{ ...likely, outcome: outcome(1) }]);
    await page.reload();
    await expect(cardById(page, "cl-likely").getByTestId("gap-partly-covered")).toHaveText("Partly covered");
    await expect(cardById(page, "cl-likely").getByTestId("gap-likely-match")).toHaveCount(0);
  });

  test("ruling B-3: after a reload, clicking a cluster that waits on a follow-up resumes it", async ({ page }) => {
    const waiting = recordCluster("cl-waiting", "Container Orchestration", {
      gaps: ["Helm"],
      outcome: outcome(1, ["Kubernetes"]),
      coverage: "partly_covered",
      budget_remaining: 1,
      member_statuses: facts([["Helm", "gap"], ["Kubernetes", "covered"]]),
    });
    await setupRecordMocks(page, { row: recordAnalysis([waiting]) });
    await page.route("**/api/session", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          session_id: "micro-waiting",
          question: "And Helm — did you write your own charts?",
          choices: ["Wrote our own charts", "Adapted existing charts"],
          resumed: true,
        }),
      })
    );
    let answeredTo = "";
    await page.route("**/api/session/*/message", (route) => {
      answeredTo = new URL(route.request().url()).pathname;
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ complete: true }) });
    });
    await page.goto(`/flow/${FLOW_ID}/gaps`);
    const card = cardById(page, "cl-waiting");
    await card.click();
    await expect(card.getByTestId("gap-question")).toHaveText("And Helm — did you write your own charts?");
    await expect(card.getByTestId("gap-follow-up-label")).toHaveText("Follow-up — still open: Helm");
    await card.getByTestId("gap-choice").first().click();
    await card.getByTestId("gap-submit-button").click();
    await expect.poll(() => answeredTo).toBe("/api/session/micro-waiting/message");
  });

  test.describe("at 390 px", () => {
    test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

    test("the inline follow-up fits the card without horizontal overflow", async ({ page }) => {
      const state = { row: recordAnalysis(RECORD_CLUSTERS) };
      await setupRecordMocks(page, state);
      await page.route("**/api/session", (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ session_id: "micro-390", question: "How did you run containers?", choices: null }),
        })
      );
      await page.route("**/api/session/micro-390/message", (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            complete: false,
            question: "And Helm — did you write your own charts?",
            choices: ["Wrote our own charts for every internal service we ran", "Adapted existing charts"],
            cluster_coverage: { cluster_id: "cl-open", coverage: "partly_covered", open_concepts: ["Helm"], budget_remaining: 1 },
          }),
        })
      );
      await page.goto(`/flow/${FLOW_ID}/gaps`);
      const open = cardById(page, "cl-open");
      await open.tap();
      await open.getByTestId("gap-answer-textarea").fill("Three years of Kubernetes.");
      await open.getByTestId("gap-submit-button").tap();
      await expect(open.getByTestId("gap-follow-up-label")).toBeVisible();

      const box = await open.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(390);
      const overflow = await page.evaluate(() => ({
        scrollWidth: document.body.scrollWidth,
        innerWidth: window.innerWidth,
      }));
      expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.innerWidth);
    });
  });
});
