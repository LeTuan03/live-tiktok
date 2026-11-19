"use client";

// GatedAction — wrapper for consumer write actions. The backend is open
// access, so every wrapped action fires `onAction` directly on click.
// The wrapper is kept as a single seam so a gate can be reinstated here
// later without touching every call site.

import { Children, cloneElement, isValidElement, type ReactElement } from "react";

interface GatedActionProps {
  children: ReactElement<{ onClick?: (event: unknown) => void }>;
  onAction: () => void;
}

export function GatedAction({ children, onAction }: GatedActionProps) {
  const child = Children.only(children);
  if (!isValidElement(child)) {
    throw new Error("GatedAction requires a single React element child");
  }
  return cloneElement(child, { onClick: onAction });
}
