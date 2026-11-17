# Manager card: empty carry-over state

Status: built 2026-11-16.

On the manager card, carried-over items sit in a folded panel. When a team carried nothing over, a folded panel that opens onto one sentence hides good news. So the empty state is a visible panel that does not fold:

```html
<div class="carry-empty"><h3>Carried over items (0)</h3><p class="note">No work items carried over.</p></div>
```

`.carry-empty` uses the paper background with a 1px line border and 6px radius, like the folded panels. The DM card keeps its own empty state (`<div><h3>Carried over</h3><p class="note">No work items carried over.</p></div>`), with no `carry-empty` class.

Test: `test_manager_empty_carryover_has_visible_panel`.
