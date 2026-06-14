import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Lightweight client-side email check for inline UX feedback. The server's
 * pydantic `EmailStr` is authoritative — this only lets us flag obviously
 * malformed input before a round-trip, never relaxes the server's rule.
 */
export function isValidEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim())
}
