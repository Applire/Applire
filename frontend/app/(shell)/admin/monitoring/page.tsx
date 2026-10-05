"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Administration → Monitoring: the instance-health panel (E060/US312; moved here
 * from Erscheinungsbild by ruling w4-2b-1) above the monitoring (probe) tokens
 * that read the same `/api/ops/health` (US329).
 */

import { OperatorPanel } from "@/components/admin/operator-panel";
import { MonitoringTokensCard } from "@/components/admin/users/MonitoringTokensCard";

export default function AdminMonitoringPage() {
  return (
    <div className="flex flex-col gap-5">
      <OperatorPanel />
      <MonitoringTokensCard />
    </div>
  );
}
