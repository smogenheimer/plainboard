import type { Agent, ProviderInfo } from "../api/types";

/** Model sizes in order (`ModelInfo.tier`): the strength an agent names instead of a model. */
export const SIZES = ["small", "medium", "large", "xlarge"] as const;
/** The classes every installation has; more are the user's (setting `model_classes`). */
export const FIXED_CLASSES = ["allround", "coding", "vision"];
/** class → provider → model → score (0..100). */
export type ModelClasses = Record<string, Record<string, Record<string, number>>>;

export function classNames(classes?: ModelClasses | null): string[] {
  return [...new Set([...FIXED_CLASSES, ...Object.keys(classes ?? {})])];
}

/** The provider's smallest model, e.g. for summaries and connection checks. */
export function smallModel(provider?: ProviderInfo): string | undefined {
  return provider?.models.find((m) => m.tier === "small")?.id ?? provider?.models[0]?.id;
}

/** The target provider's model of the same size (tier) as `model`, which may belong to any provider;
    without one of that size its next smaller one, as the server does (`services/model_choice.py`). */
export function equivalentModel(providers: ProviderInfo[], model: string, target: string): string {
  const models = providers.find((p) => p.name === target)?.models ?? [];
  if (!model || !models.length || models.some((m) => m.id === model)) return model;
  const tier = providers.flatMap((p) => p.models).find((m) => m.id === model)?.tier ?? "medium";
  const at = Math.max(SIZES.indexOf(tier as (typeof SIZES)[number]), 0);
  const order = [...SIZES.slice(0, at + 1).reverse(), ...SIZES.slice(at + 1)];
  for (const size of order) {
    const found = models.find((m) => m.tier === size);
    if (found) return found.id;
  }
  return models[0].id;
}

/** What an agent runs on, for lists: its model, or its class and strength. */
export function modelLabel(agent: Pick<Agent, "model" | "model_class" | "strength">): string {
  return agent.model || `${agent.model_class ?? "allround"} / ${agent.strength ?? "medium"}`;
}
