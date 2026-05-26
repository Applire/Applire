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
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";

interface FileChipProps extends React.HTMLAttributes<HTMLDivElement> {
  filename: string;
  size?: number;
  progress?: number;
  onRemove: () => void;
}

function formatFileSize(bytes?: number): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const FileChip = React.forwardRef<HTMLDivElement, FileChipProps>(
  ({ filename, size, progress, onRemove, className, ...props }, ref) => {
    const t = useTranslations("common");
    const isUploading = progress !== undefined && progress < 100;

    return (
      <div
        ref={ref}
        className={cn(
          "flex items-center gap-3 rounded-lg bg-teal/10 px-3 py-2 transition-colors",
          isUploading && "bg-teal/5",
          className
        )}
        {...props}
      >
        {/* File icon */}
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded bg-teal/20">
          <svg
            className="h-4 w-4 text-teal"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
            />
          </svg>
        </div>

        {/* File info */}
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-neutral-dark truncate">
            {filename}
          </p>
          {size !== undefined && (
            <p className="text-xs text-gray-500">
              {formatFileSize(size)}
            </p>
          )}
          {isUploading && (
            <div className="h-1 w-full bg-gray-200 rounded mt-1 overflow-hidden">
              <div
                className="h-full bg-teal rounded transition-all duration-300"
                style={{ width: `${progress}%` }}
              />
            </div>
          )}
        </div>

        {/* Remove button */}
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
          className="flex h-6 w-6 shrink-0 items-center justify-center rounded hover:bg-teal/20 transition-colors"
          aria-label={t("ariaRemoveFile")}
        >
          <svg
            className="h-4 w-4 text-gray-500 hover:text-critical"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M6 18L18 6M6 6l12 12"
            />
          </svg>
        </button>
      </div>
    );
  }
);
FileChip.displayName = "FileChip";

export { FileChip };