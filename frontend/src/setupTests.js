// Vitest (seit 09/2026, ersetzt Jest aus react-scripts): die bestehenden
// Tests benutzen jest.fn / jest.spyOn / jest.restoreAllMocks — vi bietet
// dieselben Funktionen.
import { vi } from "vitest";

globalThis.jest = vi;
