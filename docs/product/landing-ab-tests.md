# PAW Landing Page — A/B Headline Tests
**Persona:** Overcommitted Technical Operator (anchor: Rafe)  
**Last Updated:** 2026-02-25  
**Status:** Ready for implementation — 3 headline variants with hypotheses and success criteria  

---

## Testing Framework

**What we're testing:** Hero section headline only (above-the-fold, highest-leverage copy)  
**Control:** Current primary headline from landing-page-v1.md  
**Primary metric:** CTA click-through rate (primary button — "Get Early Access")  
**Secondary metric:** Scroll depth past Problem section (Section 2)  
**Minimum runtime:** 7 days or 500 unique visitors per variant, whichever comes first  
**Split:** 33% / 33% / 33% (three-way test, no control held out — all variants are candidates)  

---

## Variant A — Pain-First (Bottleneck Frame)

### Headline
**Stop being the bottleneck in your own project.**

### Subheadline
PAW is the AI execution layer for technical operators who have big ambitions and not enough hours. You set the direction. PAW handles the coordination, documentation, and follow-through.

### Hypothesis
Visitors who identify as overcommitted will self-select strongly on the word "bottleneck" — it names the exact problem without explaining it. High-recognition language should drive immediate resonance and faster CTA engagement.

### Why it might win
- "Bottleneck" is vocabulary the target persona already uses about themselves
- Pain-first framing creates urgency before describing the solution
- Short (7 words) — high impact in mobile/above-fold viewport

### Why it might lose
- Could read as an accusation rather than empathy
- May not communicate what PAW actually does — relies entirely on the subheadline to complete the picture
- Visitors unfamiliar with the term may not connect

### Measurement signals
- **Win signal:** CTR ≥ 15% above baseline; scroll depth to Section 3 ≥ 60%
- **Lose signal:** Bounce rate increase ≥ 10% vs other variants
- **Insight regardless of outcome:** Heatmap click pattern on "bottleneck" keyword

---

## Variant B — Outcome-First (Leverage Frame)

### Headline
**Ship more this week — without working more hours.**

### Subheadline
PAW handles the coordination, documentation, and execution overhead so you can focus on the work only you can do. Same you. More output.

### Hypothesis
The "Ship more / work less" frame directly addresses the core value proposition for time-constrained operators. "This week" adds urgency and implies fast time-to-value — which reduces risk perception and lowers the bar to signing up.

### Why it might win
- Outcome-first framing is often highest-converting for productivity tools
- "This week" signals fast activation — reduces "I'll try it later" deferral
- Avoids jargon — accessible to both technical and non-technical readers
- Directly answers "what do I get?"

### Why it might lose
- "Ship more in less time" is a common productivity-tool trope — may read as generic or unbelievable
- Doesn't differentiate PAW from simpler tools (task managers, GPT, etc.)
- Technical operators may want specifics, not promises

### Measurement signals
- **Win signal:** CTR ≥ 18% above baseline; time on page increases
- **Lose signal:** High CTA clicks but low trial activation (suggests over-promise, under-deliver)
- **Insight regardless of outcome:** Whether "this week" urgency framing improves same-session conversion

---

## Variant C — Identity-First (Founder Peer Frame)

### Headline
**Rafe is a CS student shipping a backend, a pitch deck, and a GTM plan — in the same week.**

### Subheadline
Not by working harder. By using PAW as his execution layer. You set the direction. PAW handles everything else that can be handled.

### Hypothesis
Naming Rafe and making the claim ultra-specific creates maximum credibility and identity match for the target persona. This variant bets that concrete social proof beats abstract promise — and that "student + founder + builder" is a recognizable identity cluster that drives self-selection.

### Why it might win
- Specific, named, inspectable claims build more trust than value propositions
- Identity match ("CS student," "building a platform") drives instant relevance for ICP
- Sets up the Agent-Built Week evidence pack as a natural next step — proof is already implied
- Unusual enough to stop scroll/skimming

### Why it might lose
- "Rafe" is unknown — social proof from an unknown person may not transfer
- Longer headline may not work above the fold on mobile
- Could feel like a case study intro rather than a conversion headline

### Measurement signals
- **Win signal:** CTR ≥ 12% above baseline; high click-through on "See How" secondary CTA
- **Lose signal:** Visitors spend time on page but don't convert — implies reading without believing
- **Insight regardless of outcome:** Whether named-persona proof outperforms abstract positioning at the hero level

---

## Test Execution Checklist

- [ ] Implement 3-way split in landing page framework (e.g., Vercel Edge, Netlify split, or manual UTM routing)
- [ ] Confirm GA4 / analytics event fires on primary CTA click (goal: "hero_cta_click")
- [ ] Set up heatmap recording for each variant (Hotjar / Microsoft Clarity)
- [ ] Define scroll-depth events at Section 2 (Problem), Section 4 (Proof), and Section 7 (CTA)
- [ ] Set minimum runtime alert (7 days or 500 visits/variant)
- [ ] Document baseline CTR before launch (if any prior traffic exists)

---

## Post-Test Decision Framework

| Outcome | Action |
|---|---|
| One variant wins clearly (≥15% CTR lift, statistically significant) | Promote winner to default; archive others with results |
| Two variants tie | Run a second test with micro-variants of the tied headlines (word-level changes) |
| No variant wins (all within noise) | Test a fundamentally different frame (e.g., "before/after" headline, question headline) |
| All variants underperform baseline | Revisit persona match — may indicate ICP mismatch, not copy failure |

---

## Iteration Backlog (future tests)

These are not in-scope for v1 but should be queued after the headline test resolves:

1. **Subheadline test** — "You set the direction" vs "One goal. Clear plan. Shipped outcome."
2. **CTA button copy test** — "Get Early Access" vs "Start Free This Week" vs "See a Real Demo First"
3. **Social proof placement test** — Metrics block above vs below the fold
4. **Hero image/video test** — Static screenshot vs animated workflow demo vs blank/text-only
