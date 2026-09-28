---
name: Groww FinTech Clean
colors:
  surface: '#f8f9ff'
  surface-dim: '#cbdbf5'
  surface-bright: '#f8f9ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#eff4ff'
  surface-container: '#e5eeff'
  surface-container-high: '#dce9ff'
  surface-container-highest: '#d3e4fe'
  on-surface: '#0b1c30'
  on-surface-variant: '#3c4a43'
  inverse-surface: '#213145'
  inverse-on-surface: '#eaf1ff'
  outline: '#6b7b72'
  outline-variant: '#bacac1'
  surface-tint: '#006c4f'
  primary: '#006c4f'
  on-primary: '#ffffff'
  primary-container: '#00d09c'
  on-primary-container: '#00533c'
  inverse-primary: '#2fe0aa'
  secondary: '#545f73'
  on-secondary: '#ffffff'
  secondary-container: '#d5e0f8'
  on-secondary-container: '#586377'
  tertiary: '#855300'
  on-tertiary: '#ffffff'
  tertiary-container: '#fda417'
  on-tertiary-container: '#673f00'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#59fdc5'
  primary-fixed-dim: '#2fe0aa'
  on-primary-fixed: '#002116'
  on-primary-fixed-variant: '#00513b'
  secondary-fixed: '#d8e3fb'
  secondary-fixed-dim: '#bcc7de'
  on-secondary-fixed: '#111c2d'
  on-secondary-fixed-variant: '#3c475a'
  tertiary-fixed: '#ffddb8'
  tertiary-fixed-dim: '#ffb95f'
  on-tertiary-fixed: '#2a1700'
  on-tertiary-fixed-variant: '#653e00'
  background: '#f8f9ff'
  on-background: '#0b1c30'
  surface-variant: '#d3e4fe'
typography:
  headline-xl:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '700'
    lineHeight: 40px
  headline-lg:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
  headline-md:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
  headline-sm:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  body-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
  label-lg:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '600'
    lineHeight: 20px
  label-md:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
  label-sm:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: '500'
    lineHeight: 14px
    letterSpacing: 0.02em
  numeric-stat:
    fontFamily: Inter
    fontSize: 22px
    fontWeight: '600'
    lineHeight: 28px
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  gutter: 1rem
  margin: 1rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 0.75rem
  space-lg: 1rem
  space-xl: 1.5rem
---

## Brand & Style

This design system embodies trust, clarity, effortless investing, and institutional-grade transparency. Built specifically for retail wealth, mutual funds, stocks, and personal finance, the aesthetic pairs direct modern simplicity with accessible human warmth.

The visual style is **Corporate / Modern Minimalist** optimized for dense financial data:
- **Purity & Canvas Hierarchy**: Cool off-white background (`#F4F6F8`) acts as a quiet stage for raised, bright white (`#FFFFFF`) card surfaces.
- **Precision Color Accents**: Growth, positive returns, and primary actions are driven by a distinctive vibrant teal. Alert contexts utilize warm amber, while volatility and dips leverage clear crimson tones.
- **Clutter-Free Architecture**: Elimination of heavy drop shadows and decorative gradients in favor of structured borders, purposeful typographic hierarchy, and scannable tabular lists.

## Colors

The palette establishes an immediate sense of financial growth, liquidity, and security.

- **Primary (`#00D09C`)**: The core brand teal used for primary interactive states, confirmations, positive market movements, buy CTAs, and active navigation indicators. Hover/Pressed state: `#00B386`.
- **Secondary (`#1E293B`)**: Deep slate navy serving as high-contrast primary typography, title headers, and dark badge fills for premium instruments.
- **Tertiary (`#F59E0B`)**: Amber gold applied to caution states, pending transaction tags, regulatory/KYC notices, and advisory highlights.
- **Neutral (`#64748B`)**: Slate gray providing secondary text, muted metadata, metric labels, and inactive icon fills.
- **Surface & Backgrounds**:
  - App Canvas / Scaffold: `#F4F6F8` (Light cool tint for eye comfort).
  - Primary Surface / Card: `#FFFFFF` (Pure white).
  - Border & Dividers: `#E5E7EB` (Subtle boundary demarcation).
- **Semantic Market Color**:
  - Negative / Market Drop: `#EF4444`.

## Typography

Typographic execution relies on **Inter** across all roles to maximize legibility for tabular numbers, portfolio balances, and technical market data.

- **Tabular Figures**: All monetary statistics, P&L rates, and percentages must enable OpenType tabular numbers (`tnum`) to maintain clean columnar vertical alignment across ticker lists.
- **Visual Weighting**: Headlines remain tight with negative tracking (`-0.01em` to `-0.02em`) to convey structural strength. Metadata labels (`label-sm`) leverage slight letter-spacing (`+0.02em`) and medium weight to ensure legibility on mobile viewports.
- **Hierarchy Rules**: Primary labels (such as instrument names) use 14px SemiBold in `#1E293B`, while sub-metrics (such as "1D Return" or "Avg. NAV") use 12px Regular in `#64748B`.

## Layout & Spacing

The layout model is mobile-first, designed around vertical scanning, bottom navigation, and full-width card structures.

- **Screen Canvas**: Standard 16px (`1rem`) horizontal outer margin across mobile devices, expanding to 24px on tablet viewports.
- **Grid Architecture**: Content utilizes a 4-column fluid layout on mobile viewports and transitions into an 8-column layout on tablet.
- **Vertical Rhythm**:
  - Compact stack spacing (`0.5rem`) separates related data points (e.g., metric label to numerical value).
  - Component block spacing (`1rem`) separates disparate cards, stock rows, and action segments.
  - Section spacing (`1.5rem`) divides major modules such as "Indices Overview", "Holdings", and "Watchlist".

## Elevation & Depth

Visual depth is strictly restrained to prevent visual noise during rapid financial decision-making:

- **Low-Contrast Outlines & Tonal Separation**: Depth is predominantly conveyed through tone—pure `#FFFFFF` cards resting on `#F4F6F8` canvas, bound by a crisp 1px `#E5E7EB` border.
- **Level 0 (Flat / Inset)**: Applied to input containers, secondary metric chips, and table headers. Background `#F8FAFC` or `#F1F5F9`, no shadow.
- **Level 1 (Card Standard)**: Applied to stock cards, portfolio summaries, and quick-action tiles. Background `#FFFFFF`, 1px solid border `#E5E7EB`, with a whisper ambient drop shadow: `0 1px 3px rgba(15, 23, 42, 0.04)`.
- **Level 2 (Floating Action & Overlays)**: Applied to sticky bottom trade bars, floating quick-search triggers, and modals. Background `#FFFFFF`, 1px border `#E5E7EB`, shadow: `0 8px 24px rgba(15, 23, 42, 0.08)`.

## Shapes

The design uses a refined corner radius hierarchy that strikes a balance between professional software and approachable modern consumer tech:

- **Cards & Data Containers**: Formed with 12px to 16px radius (`rounded-lg` / `rounded-xl`), creating distinct, friendly modules.
- **Buttons & Search Bars**: Standardized at 8px to 10px radius for actionable density without looking toy-like.
- **Badges, Tags & Chips**: Built with a full pill form (`9999px`) to immediately distinguish clickable category filters, exchange tags (NSE / BSE), and percentage changes from structured rectangular cards.

## Components

### Buttons
- **Primary CTA**: Height 48px, background `#00D09C`, text `#FFFFFF`, font weight 600 (Inter), border-radius 8px. Hover/Pressed: `#00B386`.
- **Secondary CTA**: Height 48px, background `#F1F5F9`, text `#1E293B`, border 1px solid `#E2E8F0`, border-radius 8px.
- **Buy / Sell Action Bars**: Dual split layout pinned to mobile bottom; "BUY" (`#00D09C`), "SELL" (`#EF4444`), both rendered with white bold typography.

### Stock & Holding List Items
- **Structure**: 64px min-height container, background `#FFFFFF`, border-bottom 1px solid `#F1F5F9`.
- **Left Column**: Company logo/avatar (40px, rounded-md with 1px border), Instrument Name (`label-lg`, `#1E293B`), Exchange Tag (`label-sm`, `#64748B`).
- **Right Column**: Current Price (`numeric-stat` / `label-lg`, `#1E293B`), Return metric pill (`label-sm`, text `#00D09C` or `#EF4444`, background matching color at 10% opacity).

### Chips & Filter Pills
- **Unselected**: Background `#FFFFFF`, border 1px solid `#E5E7EB`, text `#64748B`, border-radius `9999px`, padding `6px 14px`.
- **Selected**: Background `#1E293B`, border 1px solid `#1E293B`, text `#FFFFFF`, border-radius `9999px`, padding `6px 14px`.

### Badges & Notices
- **Regulatory / KYC Alert**: Background `#FEF3C7` (Amber tint), border 1px solid `#FDE68A`, icon and text `#D97706`, border-radius 8px, padding `10px 14px`.
- **Market Status (Open/Closed)**: Pill badge with 6px dot indicator; Green (`#00D09C`) when market is live, Slate (`#94A3B8`) when closed.

### Input Fields
- **Container**: Height 48px, background `#FFFFFF`, border 1px solid `#E5E7EB`, border-radius 8px, padding `0 14px`.
- **Active / Focus**: Border 1.5px solid `#00D09C`, outline none.
- **Monetary Inputs**: Fixed currency symbol (`₹`) on left in `#64748B`, dynamic bold typography for entered amounts.

### Data Cards
- Pure white `#FFFFFF` surface, 12px radius, 1px solid `#E5E7EB` border, 16px internal padding. Card header contains categorical subtitle above primary metric.