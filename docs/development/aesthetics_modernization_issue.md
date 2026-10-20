## Feature: Modernize Dashboard Aesthetics to Premium Standards

### Summary

Overhaul the Runner Dashboard visual design to meet modern premium web application standards. The current interface should be upgraded with sleek, contemporary aesthetics including glassmorphism effects, refined typography, smooth animations, vibrant gradients, and responsive layout improvements.

---

### Motivation

- **First Impressions**: The dashboard is the primary interface for monitoring fleet operations. A premium look builds confidence in the underlying system.
- **Modern Standards**: Current web design trends emphasize depth, motion, and spatial awareness through layered surfaces, micro-animations, and refined color usage.
- **Consistency**: Align with the fleet's shared design token system for a cohesive cross-application experience.

---

### Design Targets

#### Typography

- [ ] Replace default fonts with modern web fonts (Inter, JetBrains Mono for code)
- [ ] Establish clear typographic hierarchy (display, heading, body, caption, code)
- [ ] Use fluid typography scaling (`clamp()` for responsive font sizes)

#### Color & Surface

- [ ] Implement glassmorphism for cards and overlays (`backdrop-filter: blur()`)
- [ ] Use smooth gradients for header/hero sections
- [ ] Add depth through layered surfaces with subtle shadows
- [ ] Implement vibrant accent colors with soft glow effects
- [ ] Use HSL-based color palette for harmonious color relationships
- [ ] Integrate fleet shared themes via CSS custom properties from `themes.json`

#### Animation & Interaction

- [ ] Add page transition animations (fade, slide)
- [ ] Implement micro-animations for buttons, cards, and interactive elements
- [ ] Add skeleton loading states for async data
- [ ] Hover effects with scale transforms and shadow elevation
- [ ] Smooth scrolling and scroll-triggered animations
- [ ] Status indicators with subtle pulse animations

#### Layout & Responsive

- [ ] Implement fluid grid layouts with CSS Grid
- [ ] Add responsive breakpoints for tablet and mobile
- [ ] Use container queries for component-level responsiveness
- [ ] Add collapsible sidebar with smooth transitions
- [ ] Implement card-based dashboard layout with drag-to-reorder

#### Data Visualization

- [ ] Modern chart styling with gradients and smooth curves
- [ ] Interactive tooltips with glassmorphism effect
- [ ] Animated chart transitions on data updates
- [ ] Consistent color palette from shared chart colors

---

### Design Inspiration

- Linear.app — Clean, minimal, glassmorphic
- Vercel Dashboard — Premium dark mode, smooth animations
- Raycast — Refined typography, micro-interactions
- Supabase Dashboard — Clear hierarchy, modern cards

### Files to Modify

- `frontend/src/index.css` — Complete design system overhaul
- `frontend/src/components/**` — Component-level styling updates
- `frontend/src/design/` — Design token integration
- `frontend/index.html` — Google Fonts, meta viewport

### Acceptance Criteria

- [ ] Modern typography with Inter/JetBrains Mono fonts
- [ ] Glassmorphism effects on cards and modals
- [ ] Smooth page and component transitions
- [ ] Responsive layout works on desktop, tablet, mobile
- [ ] All colors sourced from shared theme system CSS variables
- [ ] Skeleton loading states for all async data views
- [ ] WCAG AA accessibility compliance maintained
- [ ] Lighthouse performance score >= 90
