import { afterEach, describe, expect, it } from 'vitest';
import { MODELS, PROVIDERS, sameTierModel, type ModelType } from './models';

describe('sameTierModel', () => {
  it.each([
    ['gpt-5.6-luna', 'claude-haiku-4-5'],
    ['gpt-5.6-terra', 'claude-sonnet-5'],
    ['gpt-6-astra-medium', 'claude-fable-5-1-medium'],
    ['gpt-6-astra-high', 'claude-fable-5-1-high'],
    ['gpt-6-astra-max', 'claude-fable-5-1-max'],
  ] as const)('keeps the thinking position: %s on Claude is %s', (gpt, claude) => {
    expect(sameTierModel(gpt, 'anthropic')).toBe(claude);
    expect(sameTierModel(claude, 'openai')).toBe(gpt);
  });

  it('returns the model itself on its own provider', () => {
    for (const model of MODELS) {
      expect(sameTierModel(model.id, model.provider)).toBe(model.id);
    }
  });

  it('always lands on a model of the target provider', () => {
    for (const model of MODELS) {
      for (const provider of PROVIDERS) {
        const target = MODELS.find(m => m.id === sameTierModel(model.id, provider));
        expect(target?.provider).toBe(provider);
      }
    }
  });

  describe('when the target provider has fewer models', () => {
    // Simulates a provider losing its deepest tier (a retirement with no
    // same-depth replacement) by taking it out of the live catalogue.
    const catalogue = MODELS as unknown as (typeof MODELS)[number][];
    let removed: { index: number; model: (typeof MODELS)[number] } | null = null;

    const withoutModel = (id: ModelType) => {
      const index = catalogue.findIndex(m => m.id === id);
      removed = { index, model: catalogue.splice(index, 1)[0] };
    };

    afterEach(() => {
      if (removed) catalogue.splice(removed.index, 0, removed.model);
      removed = null;
    });

    it('clamps a deeper choice to the deepest model it has', () => {
      withoutModel('claude-fable-5-1-max');
      expect(sameTierModel('gpt-6-astra-max', 'anthropic')).toBe('claude-fable-5-1-high');
    });

    it('keeps positions the shorter provider still has', () => {
      withoutModel('claude-fable-5-1-max');
      expect(sameTierModel('gpt-6-astra-high', 'anthropic')).toBe('claude-fable-5-1-high');
      expect(sameTierModel('gpt-5.6-luna', 'anthropic')).toBe('claude-haiku-4-5');
    });
  });
});
