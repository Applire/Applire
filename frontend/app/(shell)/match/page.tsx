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


import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { JobCard, type JobMatchResult } from "@/components/match/JobCard";
import { AppTopbar } from "@/components/shell/AppTopbar";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

interface GapSummary {
  critical_gaps: string[];
  strengths: string[];
}

interface EnrichedJob extends JobMatchResult {
  strengths: string[];
  gaps: string[];
}

async function fetchGapSummary(gapAnalysisId: string): Promise<GapSummary | null> {
  // Gap analyses are stored — we fetch the analysis detail from the job gap endpoint.
  // The match API doesn't return gap detail inline, so we skip enrichment when not available.
  return null;
}

export default function MatchPage() {
  const router = useRouter();
  const [jobs, setJobs] = useState<EnrichedJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch(`${API_BASE}/api/jobs/match?top_n=20`);
        if (res.status === 404) {
          // No profile yet — redirect to onboarding
          router.replace("/");
          return;
        }
        if (!res.ok) {
          setError("Failed to load job matches. Please try again.");
          return;
        }
        const data: JobMatchResult[] = await res.json();
        setJobs(data.map((j) => ({ ...j, strengths: [], gaps: [] })));
      } catch {
        setError("Could not reach the server. Is the backend running?");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [router]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-surface-dim" data-testid="match-loading">
        <p className="text-gray-500">Loading matches…</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col flex-1 overflow-hidden">
      <AppTopbar mode="section" titleKey="match.title" />

      <main className="flex-1 overflow-y-auto px-8 py-7">
        {error && (
          <div
            className="p-4 rounded-lg bg-critical/10 border border-critical/20 mb-6"
            data-testid="match-error"
          >
            <p className="text-sm text-critical">{error}</p>
          </div>
        )}

        {!error && jobs.length === 0 && (
          <div
            className="text-center py-20"
            data-testid="match-empty-state"
          >
            <div className="text-4xl mb-4">📋</div>
            <h2 className="font-heading text-xl font-semibold text-neutral-dark mb-2">
              No jobs analysed yet
            </h2>
            <p className="text-gray-500 mb-6 max-w-md mx-auto">
              Paste a job description on the home page to analyse it and see it ranked here.
            </p>
            <button
              type="button"
              onClick={() => router.push("/")}
              className="inline-flex items-center px-5 py-2.5 rounded-lg bg-teal text-white text-sm font-semibold hover:bg-teal/90 transition-colors"
            >
              Add your first job →
            </button>
          </div>
        )}

        {jobs.length > 0 && (
          <div className="flex flex-col gap-4" data-testid="job-card-list">
            {jobs.map((job) => (
              <JobCard
                key={job.job_id}
                job={job}
                strengths={job.strengths}
                gaps={job.gaps}
              />
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
