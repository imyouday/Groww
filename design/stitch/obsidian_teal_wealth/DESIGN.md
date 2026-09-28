---
name: Obsidian Teal Wealth
colors:
  surface: '#131313'
  surface-dim: '#131313'
  surface-bright: '#393939'
  surface-container-lowest: '#0e0e0e'
  surface-container-low: '#1c1b1b'
  surface-container: '#201f1f'
  surface-container-high: '#2a2a2a'
  surface-container-highest: '#353534'
  on-surface: '#e5e2e1'
  on-surface-variant: '#bacac1'
  inverse-surface: '#e5e2e1'
  inverse-on-surface: '#313030'
  outline: '#85948c'
  outline-variant: '#3c4a43'
  surface-tint: '#2fe0aa'
  primary: '#44edb7'
  on-primary: '#003828'
  primary-container: '#00d09c'
  on-primary-container: '#00533c'
  inverse-primary: '#006c4f'
  secondary: '#50ddad'
  on-secondary: '#003828'
  secondary-container: '#01b386'
  on-secondary-container: '#003d2c'
  tertiary: '#ffc98a'
  on-tertiary: '#472a00'
  tertiary-container: '#fda417'
  on-tertiary-container: '#673f00'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#59fdc5'
  primary-fixed-dim: '#2fe0aa'
  on-primary-fixed: '#002116'
  on-primary-fixed-variant: '#00513b'
  secondary-fixed: '#71fac8'
  secondary-fixed-dim: '#50ddad'
  on-secondary-fixed: '#002116'
  on-secondary-fixed-variant: '#00513b'
  tertiary-fixed: '#ffddb8'
  tertiary-fixed-dim: '#ffb95f'
  on-tertiary-fixed: '#2a1700'
  on-tertiary-fixed-variant: '#653e00'
  background: '#131313'
  on-background: '#e5e2e1'
  surface-variant: '#353534'
typography:
  display-lg:
    fontFamily: Inter
    fontSize: 40px
    fontWeight: '700'
    lineHeight: 48px
    letterSpacing: -0.03em
  headline-xl:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-xl-mobile:
    fontFamily: Inter
    fontSize: 26px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Inter
    fontSize: 22px
    fontWeight: '600'
    lineHeight: 28px
    letterSpacing: -0.015em
  headline-sm:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
    letterSpacing: -0.01em
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
    letterSpacing: -0.005em
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
    letterSpacing: 0em
  body-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
    letterSpacing: 0em
  label-numeric:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '600'
    lineHeight: 20px
    letterSpacing: -0.01em
  label-badge:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: '600'
    lineHeight: 14px
    letterSpacing: 0.04em
  label-caption:
    fontFamily: Inter
    fontSize: 10px
    fontWeight: '500'
    lineHeight: 12px
    letterSpacing: 0.02em
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  gutter: 1rem
  gutter-desktop: 1.5rem
  margin: 1rem
  margin-tablet: 1.5rem
  margin-desktop: 2.5rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 1rem
  space-lg: 1.5rem
  space-xl: 2rem
---

## Brand & Style

This design system expresses clarity, velocity, and institutional trust in a high-density financial ecosystem. The visual language blends minimal technical precision with atmospheric dark-mode ergonomics, catering to modern retail investors, options traders, and wealth-builders who require low-latency legibility during intensive market hours.

Key stylistic principles:
- **Atmospheric Restraint:** Heavy, deep charcoal foundations absorb visual fatigue, avoiding pure #000000 voids in favor of layered, warm-slate black (#121212) that preserves structural depth.
- **Luminescent Accentuation:** The signature teal acts as a high-visibility beacon for upward momentum, order execution, and critical calls-to-action.
- **Sleek FinTech Modernism:** Sharp informational density balanced with generous tap-targets, soft pill-like curvature, crisp 1px borders, and deliberate micro-hierarchy.

## Colors

The palette is engineered specifically for OLED/AMOLED efficiency, reduced eye-strain, and rapid scanning of financial metrics.

### Core Canvas & Surfaces
- **Canvas Base (`bg-canvas`):** `#121212` — Root viewport background.
- **Surface Level 1 (`bg-surface`):** `#1E1E1E` — Primary panels, structural tabs, and base navigation wrappers.
- **Surface Level 2 (`bg-surface-elevated`):** `#252525` — Interactive stock/fund cards, nested list tiles, modal sheets.
- **Surface Level 3 (`bg-surface-active`):** `#2C2C2C` — Hover states, pressed states, and nested data pods.

### Outlines & Dividers
- **Subtle Border (`border-subtle`):** `#2E2E2E` — Passive card separators, table row rules.
- **Active / Structural Border (`border-strong`):** `#333333` — Input frames, active tab lines, divider edges.

### Typography & Content
- **Text High Emphasis (`text-primary`):** `#F3F4F6` — Primary values, stock tickers, account balances, main headings.
- **Text Medium Emphasis (`text-secondary`):** `#9CA3AF` — Subheaders, axis labels, field captions, secondary indicators.
- **Text Low Emphasis / Muted (`text-muted`):** `#6B7280` — Disclaimers, trailing timestamps, placeholder labels.

### Brand & Market Semantics
- **Brand Teal / Market Positive:** `#00D09C` (Hover: `#00B386`, Tint Base: `rgba(0, 208, 156, 0.12)`) — Primary actions, positive gains, active indicators.
- **Market Negative:** `#EB5757` (Hover: `#D63F3F`, Tint Base: `rgba(235, 87, 87, 0.12)`) — Downward moves, destructive actions.
- **Warning & Compliance Alert:** Border and text `#F59E0B` over a dedicated protective base `#2A2118` — Regulatory notices, margin calls, KYC verification nudges.

## Typography

The type hierarchy prioritizes tabular consistency and rapid scanning.

- **Tabular Numerics:** All currency counts, portfolio metrics, stock prices, and percentages must enable `font-feature-settings: "tnum" 1` to eliminate horizontal jitter during live streaming ticks.
- **Letter Spacing:** Tighter letter-spacing is applied to larger displays and headlines (`-0.03em` to `-0.015em`) for modern density. Badges and micro-captions expand outward (`0.02em` to `0.04em`) to ensure legibility when rendered in all-caps or pill formats.
- **Line Heights:** Compact line heights prevent excess vertical sprawl in data-dense table lists and order panels.

## Layout & Spacing

The layout is grounded in a fluid responsive grid structured around a consistent 4px/8px incremental base:

- **Desktop (1024px+):** 12-column grid, fluid width capped at `1440px`. Column gutters set to `gutter-desktop` (24px), canvas edge margins set to `margin-desktop` (40px). Side navigation spans 3 columns or a static 260px rail; content fills the remainder.
- **Tablet (768px – 1023px):** 8-column layout with 16px gutters and 24px canvas margins. Multi-column portfolios reflow into 2-column stacked clusters.
- **Mobile (320px – 767px):** 4-column layout with 12px or 16px gutters and 16px canvas padding. Action bars (Buy/Sell, Place Order) pin permanently to the viewport bottom with safe-area spacing.

Component interiors enforce strict rhythm: compact items (pills, badges, micro inputs) employ `space-xs` (4px) to `space-sm` (8px), while cards and data modules use `space-md` (16px) or `space-lg` (24px) inner paddings.

## Elevation & Depth

Visual hierarchy does not rely on bright, diffuse white drop-shadows. Instead, depth is articulated through **tonal surface stacking** combined with **subtle dark ambient occlusions** and **crisp 1px boundary lines**.

1. **Base Layer (Elevation 0):** `#121212` — Flat background, transparent dividers.
2. **Card & Section Layer (Elevation 1):** `#1E1E1E` — Bordered by `1px solid #2E2E2E`. Soft ambient shadow: `box-shadow: 0px 4px 16px rgba(0, 0, 0, 0.4)`.
3. **Interactive & Hover Layer (Elevation 2):** `#252525` — Bordered by `1px solid #333333`. Elevated shadow: `box-shadow: 0px 8px 24px rgba(0, 0, 0, 0.6)`.
4. **Overlay / Drawer / Bottom Sheet Layer (Elevation 3):** `#1E1E1E` or `#252525` with `box-shadow: 0px 16px 40px rgba(0, 0, 0, 0.85)` and an explicit top/full outline of `1px solid #333333`. Backdrops use `rgba(0, 0, 0, 0.7)` with an `8px` blur.

## Shapes

The design system maintains a refined, approachable geometry utilizing smooth curvature (12px to 16px) across cards, inputs, and modals, transitioning to full pills for contextual tags and key CTA buttons.

- **Primary Cards & Containers:** `rounded-lg` (16px) — Smooth structural edges that soften dark container borders.
- **Action Buttons & Form Controls:** `rounded-md` (8px to 12px) for inputs; full pill radius (`9999px`) is reserved for primary floating order triggers, chip filters, and ticker status tags.
- **Regulatory Badges & Mini Tags:** `rounded-sm` (6px) or pill (`9999px`) with strictly controlled padding to retain compactness.

## Components

### Buttons
- **Primary Action (Buy / Invest / Continue):** Solid `#00D09C` background with `#121212` high-contrast bold text. Hover shifts to `#00B386`. Active compression scale `0.98`. Height 44px (mobile: 48px), border-radius 12px or full pill.
- **Secondary / Ghost:** Outlined with `1px solid #333333`, background transparent or `#1E1E1E`, text `#F3F4F6`. Hover triggers background `#252525` and border `#00D09C`.
- **Sell / Destructive:** Solid or tinted red (`#EB5757` text on `rgba(235, 87, 87, 0.12)` surface) transitioning to full `#EB5757` solid for final confirm steps.

### Chips & Filter Tabs
- Container `#1E1E1E`, outline `1px solid #2E2E2E`, text `#9CA3AF`, radius `9999px`.
- Selected state: Border `#00D09C`, text `#00D09C`, background `rgba(0, 208, 156, 0.08)`.

### Form Inputs
- Background `#1E1E1E`, border `1px solid #2E2E2E`, text `#F3F4F6`, placeholder `#6B7280`, radius 12px, height 48px, horizontal padding 16px.
- Focus state: Border transitions smoothly to `#00D09C` with a micro ambient glow (`box-shadow: 0 0 0 1px #00D09C`).

### Cards & Investment Tiles
- Container `#1E1E1E` bordered by `1px solid #2E2E2E`, padding 16px, radius 16px.
- Hover state: Background subtly lightens to `#252525`, border updates to `#333333`.
- Internal dividers within cards use `#2E2E2E`.

### Selection Controls (Checkboxes & Radios)
- Unchecked: `1.5px solid #6B7280`, background transparent.
- Checked: Fill `#00D09C`, icon/dot `#121212`. Radios are circular; checkboxes use 4px corner rounding.

### Regulatory & Warning Badges
- Background `#2A2118`, border `1px solid rgba(245, 158, 11, 0.3)`, text `#F59E0B`, radius 8px, padding 8px 12px. Used for exchange alerts, settlement delays, and statutory risk warnings.

### Stock & Fund Metrics Row (List Item)
- Minimum height 64px, flex layout, border-bottom `1px solid #1E1E1E`. Left side holds company avatar/icon (radius 8px, background `#252525`) and title stack; right side holds tabular price in `#F3F4F6` and secondary gain/loss pill in `#00D09C` or `#EB5757`.