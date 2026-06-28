import type { AgentId } from "./roster";

export type CrestId = AgentId | "the-oracle";

// Inner SVG (24×24 viewBox). The outer <svg> + stroke attrs come from AgentCrest.astro.
// Form encodes the mandate; var(--agent-color) identifies the individual agent.
const ANCHOR =
  '<circle cx="12" cy="5" r="2"/><line x1="12" y1="7" x2="12" y2="19.5"/>' +
  '<line x1="8.5" y1="10" x2="15.5" y2="10"/><path d="M5 13 a7 7 0 0 0 14 0"/>' +
  '<line x1="5" y1="13" x2="3.5" y2="11.3"/><line x1="19" y1="13" x2="20.5" y2="11.3"/>';
const CROSSHAIR =
  '<circle cx="12" cy="12" r="6"/><line x1="12" y1="2.5" x2="12" y2="7"/>' +
  '<line x1="12" y1="17" x2="12" y2="21.5"/><line x1="2.5" y1="12" x2="7" y2="12"/>' +
  '<line x1="17" y1="12" x2="21.5" y2="12"/><circle cx="12" cy="12" r="1.3" fill="currentColor" stroke="none"/>';
const ROCKET =
  '<path d="M12 2.5 C14.8 5, 15 9.5, 13.8 13.5 L10.2 13.5 C9 9.5, 9.2 5, 12 2.5 Z"/>' +
  '<circle cx="12" cy="8" r="1.5"/><path d="M10.2 12.2 L7.5 15 L9.9 14.6"/>' +
  '<path d="M13.8 12.2 L16.5 15 L14.1 14.6"/><path d="M10.8 14 L12 18.8 L13.2 14"/>';
const BLOCK =
  '<polygon points="12,4 19,8 19,16 12,20 5,16 5,8"/>' +
  '<circle cx="12" cy="12" r="2.1" fill="currentColor" stroke="none"/>';
const EXCHANGE =
  '<line x1="5" y1="9.5" x2="18.5" y2="9.5"/><path d="M15.5 7 L19 9.5 L15.5 12"/>' +
  '<line x1="19" y1="14.5" x2="5.5" y2="14.5"/><path d="M8.5 12 L5 14.5 L8.5 17"/>';
const INGOT = '<path d="M8 8 H16 L19 16 H5 Z"/><line x1="9.5" y1="12" x2="14.5" y2="12"/>';
const GLOBE =
  '<circle cx="12" cy="12" r="8"/><ellipse cx="12" cy="12" rx="3.4" ry="8"/>' +
  '<line x1="4" y1="12" x2="20" y2="12"/>';
const EYE =
  '<path d="M3 12 Q12 5 21 12 Q12 19 3 12 Z"/><circle cx="12" cy="12" r="3"/>' +
  '<circle cx="12" cy="12" r="1.1" fill="currentColor" stroke="none"/>';

export const CREST_PATHS: Record<CrestId, string> = {
  "steady-eddie-eur": ANCHOR,
  "steady-eddie-usd": ANCHOR,
  "sharp-shooter-eur": CROSSHAIR,
  "sharp-shooter-usd": CROSSHAIR,
  "yolo-sapiens-eur": ROCKET,
  "yolo-sapiens-usd": ROCKET,
  satoshi: BLOCK,
  "monsieur-forex": EXCHANGE,
  goldfinger: INGOT,
  world: GLOBE,
  "the-oracle": EYE,
};

export function isCrestId(id: string): id is CrestId {
  return Object.prototype.hasOwnProperty.call(CREST_PATHS, id);
}
