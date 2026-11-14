import type { Brain } from './types.js';
import { stubBrain } from './stub.js';

// Dynamic paths so missing brain files (still being built) don't break the build.
const load = async (path: string, exp: string): Promise<Brain | null> => {
  try { return (await import(path))[exp] ?? null; } catch (e) { console.warn(`[brains] ${path} unavailable: ${(e as Error).message}`); return null; }
};

export const BRAIN_NAMES = ['scripted', 'drex', 'llm', 'stub'] as const;

export async function makeBrain(name: string): Promise<Brain> {
  if (name === 'scripted') return (await load('./scripted.js', 'scriptedBrain')) ?? stubBrain;
  if (name === 'drex') return (await load('./drex.js', 'drexBrain')) ?? stubBrain;
  if (name === 'llm') return (await load('./llm.js', 'llmBrain')) ?? stubBrain;
  return stubBrain;
}
