import type { Lang } from './strings';

interface ConversationsStrings {
  pageTitle: string;
  pageTagline: string;
  metaDescription: string;
  questionTitle: string;
  shuffle: string;
  share: string;
  copiedHint: string;
}

interface BacktesterIslandStrings {
  strategyLabel: string;
  universeLabel: string;
  startLabel: string;
  endLabel: string;
  capitalLabel: string;
  currencyLabel: string;
  runButton: string;
  warmingUp: string;
  wakingUp: string;
  unavailable: string;
  running: string;
  networkError: string;
  genericError: string;
  strategySeries: string;
  totalReturn: string;
  cagr: string;
  sharpe: string;
  maxDrawdown: string;
  vsBenchmark: string;
}

interface BacktesterStrings {
  pageTitle: string;
  pageTagline: string;
  metaDescription: string;
  island: BacktesterIslandStrings;
}

export const backtesterStrings: Record<Lang, BacktesterStrings> = {
  fr: {
    pageTitle: "Backtester — stratégies déterministes",
    pageTagline:
      "Composez une stratégie, choisissez un univers, obtenez une vraie courbe de performance.",
    metaDescription:
      "Testez une stratégie déterministe sur un univers d’actions et visualisez la courbe de performance face au MSCI World. Sans compte, sans clé API.",
    island: {
      strategyLabel: "Stratégie",
      universeLabel: "Univers",
      startLabel: "Début",
      endLabel: "Fin",
      capitalLabel: "Capital",
      currencyLabel: "Devise",
      runButton: "Lancer le backtest",
      warmingUp: "Préparation du service…",
      wakingUp:
        "Le service dormait : réveil en cours, cela peut prendre quelques minutes.",
      unavailable: "Service indisponible pour le moment.",
      running:
        "Backtest en cours — le premier lancement peut prendre quelques secondes…",
      networkError: "Échec réseau. Réessayez.",
      genericError: "Backtest échoué — essayez une fenêtre plus courte.",
      strategySeries: "Stratégie",
      totalReturn: "Rendement total",
      cagr: "CAGR",
      sharpe: "Sharpe",
      maxDrawdown: "Drawdown max",
      vsBenchmark: "vs MSCI World",
    },
  },
  en: {
    pageTitle: "Backtester — deterministic strategies",
    pageTagline:
      "Compose a strategy, pick a universe, get a real performance curve.",
    metaDescription:
      "Test a deterministic strategy over a stock universe and chart the performance curve against the MSCI World. No account, no API key.",
    island: {
      strategyLabel: "Strategy",
      universeLabel: "Universe",
      startLabel: "Start",
      endLabel: "End",
      capitalLabel: "Capital",
      currencyLabel: "Currency",
      runButton: "Run backtest",
      warmingUp: "Warming up the service…",
      wakingUp: "The service was asleep — waking it up, this can take a few minutes.",
      unavailable: "Service unavailable for now.",
      running:
        "Backtest running — the first run can take a few seconds…",
      networkError: "Network failure. Try again.",
      genericError: "Backtest failed — try a shorter window.",
      strategySeries: "Strategy",
      totalReturn: "Total return",
      cagr: "CAGR",
      sharpe: "Sharpe",
      maxDrawdown: "Max drawdown",
      vsBenchmark: "vs MSCI World",
    },
  },
};

export const conversationsStrings: Record<Lang, ConversationsStrings> = {
  fr: {
    pageTitle: "Au-delà de « ça va ? »",
    pageTagline: "Une question du jour, meilleure que « ça va ? ».",
    metaDescription: "Une meilleure question par jour, bilingue FR/EN. Sans compte, sans clé API.",
    questionTitle: "Une meilleure question",
    shuffle: "Autre",
    share: "Partager",
    copiedHint: "Copié !",
  },
  en: {
    // Curly quotes, matching editorial.ts (`conversations.pitch.tagline_en`) and the
    // question card itself — the page title used to contradict the card below it.
    pageTitle: "Beyond “how are you?”",
    pageTagline: "A daily question, better than “how are you?”.",
    metaDescription: "A better question a day, bilingual FR/EN. No account, no API key.",
    questionTitle: "A better question",
    shuffle: "Shuffle",
    share: "Share",
    copiedHint: "Copied!",
  },
};
