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

interface DropzoneProps extends Omit<React.HTMLAttributes<HTMLDivElement>, "onDrop"> {
  accept?: string;
  multiple?: boolean;
  onDrop: (files: FileList) => void;
  disabled?: boolean;
}

const Dropzone = React.forwardRef<HTMLInputElement, DropzoneProps>(
  ({ accept = ".pdf,.docx,.doc", multiple = true, onDrop, disabled, className, ...props }, ref) => {
    const t = useTranslations("dropzone");
    const [isDragOver, setIsDragOver] = React.useState(false);
    const inputRef = React.useRef<HTMLInputElement>(null);
    
    // Merge refs
    React.useImperativeHandle(ref, () => inputRef.current!);

    const handleDragOver = (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (!disabled) {
        setIsDragOver(true);
      }
    };

    const handleDragLeave = (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setIsDragOver(false);
    };

    const handleDrop = (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setIsDragOver(false);
      if (!disabled && e.dataTransfer.files.length > 0) {
        onDrop(e.dataTransfer.files);
      }
    };

    const handleClick = () => {
      if (!disabled) {
        inputRef.current?.click();
      }
    };

    const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
      if (e.target.files && e.target.files.length > 0) {
        onDrop(e.target.files);
        // Reset input to allow re-uploading same file
        e.target.value = "";
      }
    };

    return (
      <div
        className={cn(
          "relative min-h-[240px] w-full cursor-pointer rounded-lg border-2 border-dashed transition-all duration-200",
          "flex flex-col items-center justify-center gap-4 p-6 text-center",
          isDragOver
            ? "border-solid border-teal bg-teal/5"
            : "border-teal bg-neutral-light/50 hover:border-solid hover:bg-teal/5",
          disabled && "cursor-not-allowed opacity-50",
          className
        )}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={handleClick}
        {...props}
      >
        <input
          ref={inputRef}
          type="file"
          accept={accept}
          multiple={multiple}
          disabled={disabled}
          onChange={handleFileChange}
          data-testid="file-input"
          className="hidden"
        />
        
        {/* Upload icon */}
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-teal/10">
          <svg
            className="h-6 w-6 text-teal"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"
            />
          </svg>
        </div>
        
        <div>
          <p className="text-sm font-semibold text-neutral-dark">
            {t("dragDropHeading")}
          </p>
          <p className="text-sm text-gray-500 mt-1">
            {t("orPrefix")} <span className="text-teal underline">{t("clickToBrowse")}</span>
          </p>
        </div>

        <p className="text-xs text-gray-400">
          {t("formatHint")}
        </p>
      </div>
    );
  }
);
Dropzone.displayName = "Dropzone";

export { Dropzone };