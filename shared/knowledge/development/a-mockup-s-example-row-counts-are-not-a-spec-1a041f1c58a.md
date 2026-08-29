<!-- author: self; chat: fbefaa3512f84d6dbded1d577b344eed -->
The 2026-08-25 dashboard was built from a mockup that drew 3-4 rows per card, and that was implemented as a rule: fixed-height cards that clipped to a whole number of rows and named the remainder on a link. On the real instance (113 chats, 9 waiting items) it showed four and three, and the operator's reaction was that it was 'weird'. A mockup has to draw SOME number of rows; that number is a drawing constraint, not a decision. When extracting geometry from a mockup, take the proportions, the type scale and the spacing; do not take the item counts.

Same pass, same shape of error: the card wore a --warn border whenever anything was waiting. On an instance where something is always waiting, that is a permanent yellow box, not an alarm.

Fixed 2026-08-26 (28585992, fef7c914, 0c74bfb7): the card body scrolls, the row height comes from the viewport, and rowCaps/fitRows are deleted.