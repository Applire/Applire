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


import * as React from "react";
import { cn } from "@/lib/utils";

interface ProgressLinearProps extends React.HTMLAttributes<HTMLDivElement> {
  value: number;
  max?: number;
  showLabel?: boolean;
}

function ProgressLinear({
  value,
  max = 100,
  showLabel = false,
  className,
  ...props
}: ProgressLinearProps) {
  const percentage = Math.min(Math.max((value / max) * 100, 0), 100);

  return (
    <div className={cn("w-full", className)} {...props}>
      <div className="h-2 w-full rounded-full bg-gray-200 overflow-hidden">
        <div
          className="h-full bg-teal rounded-full transition-all duration-500 ease-out"
          style={{ width: `${percentage}%` }}
        />
      </div>
      {showLabel && (
        <p className="text-xs text-gray-500 mt-1 text-right">
          {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- numeric percentage display */}
          {Math.round(percentage)}%
        </p>
      )}
    </div>
  );
}

interface ProgressCircleProps extends React.HTMLAttributes<HTMLDivElement> {
  value: number;
  max?: number;
  size?: number;
  strokeWidth?: number;
  showLabel?: boolean;
  label?: string;
  color?: "success" | "warning" | "critical" | "teal";
}

function ProgressCircle({
  value,
  max = 100,
  size = 100,
  strokeWidth = 8,
  showLabel = true,
  label,
  color = "teal",
  className,
  ...props
}: ProgressCircleProps) {
  const percentage = Math.min(Math.max((value / max) * 100, 0), 100);
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (percentage / 100) * circumference;

  const colorClasses = {
    success: "text-success",
    warning: "text-warning",
    critical: "text-critical",
    teal: "text-teal",
  };

  const [displayValue, setDisplayValue] = React.useState(0);

  React.useEffect(() => {
    const duration = 1000;
    const steps = 30;
    const increment = percentage / steps;
    let current = 0;
    const timer = setInterval(() => {
      current += increment;
      if (current >= percentage) {
        setDisplayValue(percentage);
        clearInterval(timer);
      } else {
        setDisplayValue(current);
      }
    }, duration / steps);
    return () => clearInterval(timer);
  }, [percentage]);

  return (
    <div
      className={cn("relative inline-flex items-center justify-center", className)}
      style={{ width: size, height: size }}
      {...props}
    >
      <svg
        width={size}
        height={size}
        className="transform -rotate-90"
      >
        {/* Background circle */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth={strokeWidth}
          className="text-gray-200"
        />
        {/* Progress circle */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth={strokeWidth}
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          strokeLinecap="round"
          className={cn(colorClasses[color], "transition-all duration-500 ease-out")}
        />
      </svg>
      {showLabel && (
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className={cn("text-2xl font-bold", colorClasses[color])}>
            {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- numeric percentage display */}
            {Math.round(displayValue)}%
          </span>
          {label && (
            <span className="text-xs text-white mt-0.5">{label}</span>
          )}
        </div>
      )}
    </div>
  );
}

export { ProgressLinear, ProgressCircle };