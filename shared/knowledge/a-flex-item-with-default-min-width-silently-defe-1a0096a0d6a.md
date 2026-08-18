<!-- author: self; chat: 65341b404db1430583cc72aabbdb3e2d -->
Measured 2026-08-16 on the chat Work panel.

`.ws-truncate` (nowrap + overflow hidden + text-overflow: ellipsis) only produces an ellipsis if some ancestor actually constrains the width. If any ancestor between the truncating element and the capped surface is a FLEX ITEM that never got `min-width: 0`, it keeps the flex default `min-width: auto` and refuses to shrink below its max-content width. The whole subtree then sizes to the full text, spills past the capped ancestor, and `overflow: hidden` cuts it mid-word. No ellipsis ever appears, because from the element's own point of view it had all the room it asked for.

The tell in the DOM: `scrollWidth === clientWidth` on an element that is visibly cut off. If truncation were working you would see `clientWidth < scrollWidth`. Measure both plus `getBoundingClientRect().right` against the clipping ancestor's right edge; a positive spill with equal scroll/client widths is this bug, every time.

Concrete case: `.chat-workpanel` is a flex item of `.ws-popover__surface` (which caps at `max-width: min(24rem, ...)` and clips). Title box was 411px inside a 384px surface, 106.5px past the clip. Fix was `min-width: 0` on `.chat-workpanel` and `.chat-workpanel__scroll`.

Why it hid for a month: the phone sheet variant uses `position: absolute; left: 0; right: 0`, which is a DEFINITE width, so nothing shrink-wraps and the ellipsis worked there. Desktop-only truncation bugs with a fine mobile sheet are the signature.