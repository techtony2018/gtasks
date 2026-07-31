# Mission Control brand assets

## Direction

Mission Control has its own **Polaris-led navigation** symbol: a dimensional but deliberately subdued seven-star Big Dipper sits at the center of a chamfered command frame, while a brighter, ray-emitting Polaris is positioned in the upper-right along the true Merak-to-Dubhe pointer direction. The seven supporting stars use half-scale orb diameters so Polaris remains the unmistakable focal point. There is no separate task route, “M”, or face-like node cluster.

It belongs to the Memory Stargraph family through shared visual grammar—not shared artwork:

- deep-space navy `#020816`;
- Stargraph-family ice blue `#38BDF8`, starlight `#EEF4FF`, cyan detail `#22D3EE`, and violet `#A855F7`;
- fine technical linework, glow, HUD framing, and graph/navigation cues;
- `"Rajdhani", Inter, "SF Pro Text", "Segoe UI", sans-serif` typography.

The source references inspected were:

- `/Users/tony/Documents/Collective Knowledge System/public/styles.css` for the active HUD palette, typography, chamfer, and glow language;
- `/Users/tony/Documents/Collective Knowledge System/public/assets/brand/logo-circle-transparent.png` only to verify differentiation. It is **not copied or used** in these assets.

The differentiation is deliberate: Memory Stargraph is a dense circular constellation network; Mission Control is an angular frame containing the recognizable Big Dipper and one isolated guiding star. At favicon scale, the Stargraph mark reads as a round star web while Mission Control reads as a ladle-shaped seven-star asterism with Polaris above it.

## Deliverables

- `source/mission-control-command-mark.svg` — scalable primary symbol.
- `source/mission-control-lockup.svg` — scalable horizontal lockup.
- `source/favicon.svg` — optically simplified favicon source.
- `exports/mission-control-logo-128.png` — common raster logo.
- `exports/favicon-32.png` and `exports/favicon.ico` — browser icons.
- `exports/apple-touch-icon-180.png` — touch icon.
- `artwork/mission-control-word-art.png` — original 3:1 celestial “Mission Control” word artwork with Polaris centered between the two words and a subdued Big Dipper in the background.
- `preview/mission-control-review.html` — local-only responsive visual review surface.
- `preview/mission-control-review-desktop.png` — 1280 × 720 desktop review capture.
- `preview/mission-control-review-mobile-390.png` — 390 × 844 mobile review capture.
- `preview/mission-control-north-star-logo-draft.png` — centered dimensional Big Dipper, correctly placed Polaris, and favicon review board.

## Usage

- Use the full mark at 24px and above; use `favicon.svg`, `favicon-32.png`, or `favicon.ico` for browser chrome.
- Recommended sidebar/header size: 40px.
- Keep clear space equal to one chamfered corner on every side.
- Do not add an M, circular node clusters, or the Memory Stargraph roundel.
- Keep Polaris as the highest-contrast element; the Big Dipper remains quieter supporting context.
- Keep Polaris centered horizontally in the upper half of the HUD frame. Rotate the Big Dipper beneath it so the Merak-to-Dubhe pointer still reaches Polaris at five times the pointer-star spacing.
- Keep the adjacent “Mission Control” name as accessible live text in the product UI.
- Place the “Mission Control” word artwork at the bottom center of the main-content column, matching Memory Stargraph’s word-art role: `width: min(260px, 36vw)`, `aspect-ratio: 760 / 253`, and `object-fit: contain`. Keep its original lettering, constellation lines, and star field intact.

No managed dashboard or deployed service was changed.
