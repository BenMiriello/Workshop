// The filter row and its menus, and the phone's filter sheet. Both drive the
// same `TicketFilters` value; neither owns any filtering logic of its own (that
// is lib/ticketFilters.ts, which is pure and tested).
//
// The "+ Filter" menu is ONE popover grouped by field rather than a field list
// that opens a second popover per field. Two levels of popover is a fragile
// shape (two placements to flip, two dismiss rules, a submenu that has to
// survive the pointer leaving it) and it costs an extra interaction on every
// filter reached through it.
//
// STATUS IS NOT IN THAT MENU, and not in the sheet either. It is
// `TicketStatusBar`, on screen above the list at every width, because it is the
// axis a queue of ~200 tickets is read along and the only one whose answer is a
// number worth showing.
import { Plus, Search, SlidersHorizontal, ArrowDown, ArrowUp } from "lucide-react";
import {
  Button, FilterBar, FilterChip, Menu, MenuCheckItem, MenuItem, MenuLabel, Modal,
  Segmented,
} from "@workshop/ui";
import {
  TASK_SORTS, TASK_EFFORTS, TASK_SOURCES, scopeLabel, sourceLabel,
  taskStatusLabel, type TaskSort,
} from "../../lib/tasks";
import {
  STATUS_PRESETS, chipsFor, clearField, statusPresetOf, statusesForPreset,
  toggleValue,
  type TicketFilters as Filters, type TicketSort, type TicketView,
} from "../../lib/ticketFilters";

export type LabelCount = { label: string; count: number };

/** The count behind each status preset, keyed by preset value. `undefined`
 *  while it is still being counted, which is most of the first second. */
export type StatusCounts = Record<string, number | undefined>;

/** The status control, on screen at every width and above the list.
 *
 *  IT IS THE ONE CONTROL A 200-TICKET QUEUE IS ACTUALLY USED THROUGH. Status
 *  was previously reachable only by opening the "+ Filter" menu or the phone's
 *  sheet and ticking boxes, so "how much is blocked" could not be read without
 *  three interactions and could not be read at all without running the filter.
 *  Five presets, the server's own count beside each, one tap.
 *
 *  No `StatusDot` on the pills: three of the five presets are SETS of statuses
 *  ("Active" is four), so a dot there would have to pick one of them and would
 *  be picking wrong. The rows below carry the dots, where each one is about a
 *  single ticket. */
export function TicketStatusBar({ filters, counts, onFilters }: {
  filters: Filters;
  counts: StatusCounts;
  onFilters: (next: Filters) => void;
}) {
  const current = statusPresetOf(filters.status);
  return (
    <div className="ws-pillset" role="group" aria-label="Filter by status">
      {STATUS_PRESETS.map((p) => {
        const n = counts[String(p.value)];
        return (
          <button
            key={String(p.value)}
            type="button"
            className="ws-selectpill"
            aria-pressed={current === p.value}
            onClick={() => onFilters({ ...filters, status: statusesForPreset(p.value) })}
          >
            {p.label}
            {/* A count only once there is one. A 0 printed while the request is
                in flight is a claim, and it is wrong on every preset that has
                rows. */}
            {n !== undefined && <span className="ws-selectpill__count">{n}</span>}
          </button>
        );
      })}
    </div>
  );
}

type Props = {
  filters: Filters;
  sort: TicketSort;
  scopes: string[];
  labels: LabelCount[];
  assignees: LabelCount[];
  /** null when the surface does not know its total (see the board). */
  count: number | null;
  /** The surface IS one scope (an app's own Tickets tab), so scope is not a
   *  filter here: no menu group, no chip, nothing to clear. Offering "All
   *  scopes" on a list that cannot leave its scope is a control that lies. */
  lockedScope?: boolean;
  onFilters: (next: Filters) => void;
  onSort: (next: TicketSort) => void;
  onNew: () => void;
};

// The field menu, shared by the desktop bar and (as a flat body) by the sheet.
function FieldMenuBody({ filters, scopes, labels, assignees, lockedScope, onFilters }: {
  filters: Filters; scopes: string[]; labels: LabelCount[]; assignees: LabelCount[];
  lockedScope?: boolean;
  onFilters: (next: Filters) => void;
}) {
  return (
    <>
      {/* NO STATUS GROUP. Status is `TicketStatusBar`, which is on screen above
          the list at every width with a count on every preset. Listing the
          eight statuses here as well would be the same control twice, and the
          menu's version is the weaker one: it is closed by default, it takes
          three interactions, and it can show no counts. Removed at both widths
          rather than kept on the wide one, because a filter the phone and the
          desktop disagree about is two surfaces to keep in sync. */}
      {/* WHERE IT CAME FROM. First, because on this queue it is the strongest
          narrowing there is after status: 132 of 208 open tickets were filed by
          an agent noticing something while it worked. */}
      <MenuLabel>Source</MenuLabel>
      {TASK_SOURCES.map((k) => (
        <MenuCheckItem
          key={k}
          checked={filters.source.includes(k)}
          onToggle={() => onFilters(toggleValue(filters, "source", k))}
        >
          {sourceLabel(k)}
        </MenuCheckItem>
      ))}
      <MenuLabel>Effort</MenuLabel>
      {TASK_EFFORTS.map((e) => (
        <MenuCheckItem
          key={e}
          checked={filters.effort.includes(e)}
          onToggle={() => onFilters(toggleValue(filters, "effort", e))}
        >
          {e}
        </MenuCheckItem>
      ))}
      {assignees.length > 0 && (
        <>
          <MenuLabel>Assignee</MenuLabel>
          {assignees.map((a) => (
            <MenuCheckItem
              key={a.label}
              checked={filters.assignee.includes(a.label)}
              count={a.count}
              onToggle={() => onFilters(toggleValue(filters, "assignee", a.label))}
            >
              {a.label}
            </MenuCheckItem>
          ))}
        </>
      )}
      {labels.length > 0 && (
        <>
          <MenuLabel>Tag</MenuLabel>
          {labels.map((l) => (
            <MenuCheckItem
              key={l.label}
              checked={filters.labels.includes(l.label)}
              count={l.count}
              onToggle={() => onFilters(toggleValue(filters, "tag", l.label))}
            >
              {l.label}
            </MenuCheckItem>
          ))}
        </>
      )}
      {!lockedScope && (
        <>
          <MenuLabel>Scope</MenuLabel>
          {/* Single-select: a ticket lives in exactly one scope, so picking a
              second would mean "either", which no other control here offers and
              the endpoint does not take. It closes the menu, unlike the rows
              above, because choosing it is the end of the interaction. */}
          <MenuItem
            checked={!filters.scope}
            onSelect={() => onFilters({ ...filters, scope: null })}
          >
            All scopes
          </MenuItem>
          {scopes.map((s) => (
            <MenuItem
              key={s}
              checked={filters.scope === s}
              onSelect={() => onFilters({ ...filters, scope: s })}
            >
              {scopeLabel(s)}
            </MenuItem>
          ))}
        </>
      )}
    </>
  );
}

function SortMenu({ sort, onSort }: { sort: TicketSort; onSort: (s: TicketSort) => void }) {
  const label = TASK_SORTS.find((s) => s.value === sort.key)?.label ?? sort.key;
  return (
    <Menu
      label="Sort tickets"
      trigger={
        <Button size="sm" title="Sort">
          <span className="ws-sortbtn__key">Sort</span>
          {label}
          {sort.dir === "desc"
            ? <ArrowDown aria-hidden />
            : <ArrowUp aria-hidden />}
        </Button>
      }
    >
      {TASK_SORTS.map((s) => (
        <MenuItem
          key={s.value}
          checked={s.value === sort.key}
          onSelect={() => onSort({ ...sort, key: s.value as TaskSort })}
        >
          {s.label}
        </MenuItem>
      ))}
      {/* The direction is a property OF the sort, so it lives in the same menu
          rather than becoming a second control in an already busy row. */}
      <MenuLabel>Direction</MenuLabel>
      <MenuItem
        checked={sort.dir === "desc"}
        onSelect={() => onSort({ ...sort, dir: "desc" })}
      >
        Descending
      </MenuItem>
      <MenuItem
        checked={sort.dir === "asc"}
        onSelect={() => onSort({ ...sort, dir: "asc" })}
      >
        Ascending
      </MenuItem>
    </Menu>
  );
}

/** The desktop filter row. */
export function TicketFilterBar({
  filters, sort, scopes, labels, assignees, count, lockedScope,
  onFilters, onSort, onNew,
}: Props) {
  const chips = chipsFor(filters, {
    scope: scopeLabel,
    status: taskStatusLabel,
    source: sourceLabel,
  }, { lockedScope });

  return (
    <FilterBar
      search={
        <label className="ws-searchfield ws-filterbar__search">
          <Search aria-hidden />
          <input
            value={filters.q}
            onChange={(e) => onFilters({ ...filters, q: e.target.value })}
            // A COUNT ONLY WHEN THERE IS ONE. The board fetches per lane and
            // owns its own totals, so the page-level query is disabled there and
            // `count` is null -- it said "Search 0 tickets" over a full board.
            placeholder={count === null
              ? "Search tickets"
              : `Search ${count} ticket${count === 1 ? "" : "s"}`}
            aria-label="Search tickets"
          />
        </label>
      }
      chips={chips.map((c) => (
        <FilterChip
          key={c.field}
          label={c.label}
          value={c.value}
          onClear={() => onFilters(clearField(filters, c.field))}
        />
      ))}
      add={
        <Menu
          label="Add a filter"
          trigger={
            <button type="button" className="ws-addfilter">
              <Plus aria-hidden /> Filter
            </button>
          }
        >
          <FieldMenuBody
            filters={filters}
            scopes={scopes}
            labels={labels}
            assignees={assignees}
            lockedScope={lockedScope}
            onFilters={onFilters}
          />
        </Menu>
      }
      actions={
        <>
          <SortMenu sort={sort} onSort={onSort} />
          <Button size="sm" variant="primary" onClick={onNew}>
            <Plus aria-hidden /> New ticket
          </Button>
        </>
      }
    />
  );
}

/** The phone bar: search, one button into the sheet, and what is currently on. */
export function TicketFilterCompact({
  filters, sort, lockedScope, onOpen, onFilters,
}: {
  filters: Filters; sort: TicketSort;
  /** See `Props.lockedScope`. */
  lockedScope?: boolean;
  onOpen: () => void; onFilters: (next: Filters) => void;
}) {
  const chips = chipsFor(filters, {
    scope: scopeLabel, status: taskStatusLabel, source: sourceLabel,
  }, { lockedScope });
  const sortLabel = TASK_SORTS.find((s) => s.value === sort.key)?.label ?? sort.key;
  return (
    <div className="ws-ticketbar-compact">
      <div className="ws-ticketbar-compact__row">
        <label className="ws-searchfield ws-searchfield--lg">
          <Search aria-hidden />
          <input
            value={filters.q}
            onChange={(e) => onFilters({ ...filters, q: e.target.value })}
            placeholder="Search tickets"
            aria-label="Search tickets"
          />
        </label>
        <button
          type="button"
          className="ws-iconbtn-lg"
          onClick={onOpen}
          aria-label="Filter and sort"
        >
          <SlidersHorizontal aria-hidden />
        </button>
      </div>
      <div className="ws-ticketbar-compact__state">
        {chips.map((c) => (
          <FilterChip
            key={c.field}
            label={c.label}
            value={c.value}
            onClear={() => onFilters(clearField(filters, c.field))}
          />
        ))}
        <span className="ws-ticketbar-compact__sort">
          {sortLabel} {sort.dir === "desc" ? "↓" : "↑"}
        </span>
      </div>
    </div>
  );
}

/** The phone's filter and sort sheet. */
export function TicketFilterSheet({
  open, onClose, filters, sort, scopes, labels, assignees, count, lockedScope,
  onFilters, onSort, onReset,
}: {
  open: boolean; onClose: () => void;
  filters: Filters; sort: TicketSort; scopes: string[]; labels: LabelCount[];
  assignees: LabelCount[];
  /** null when the surface does not know its total (see the board). */
  count: number | null;
  /** See `Props.lockedScope`. */
  lockedScope?: boolean;
  onFilters: (next: Filters) => void;
  onSort: (next: TicketSort) => void;
  onReset: () => void;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      sheet
      title="Filter and sort"
      actions={
        <>
          <Button variant="ghost" onClick={onReset}>Reset</Button>
          <Button variant="primary" onClick={onClose}>
            Show {count} ticket{count === 1 ? "" : "s"}
          </Button>
        </>
      }
    >
      <div className="ws-filtersheet">
        {/* No Status section: `TicketStatusBar` is on the screen behind this
            sheet, so putting the same control in here would ask the reader to
            open a sheet to reach a thing already under their thumb. */}
        <section>
          <div className="ws-section-label">Source</div>
          <div className="ws-pillset">
            {TASK_SOURCES.map((k) => (
              <button
                key={k}
                type="button"
                className="ws-selectpill"
                aria-pressed={filters.source.includes(k)}
                onClick={() => onFilters(toggleValue(filters, "source", k))}
              >
                {sourceLabel(k)}
              </button>
            ))}
          </div>
        </section>

        <section>
          <div className="ws-section-label">Effort</div>
          <div className="ws-pillset">
            {TASK_EFFORTS.map((e) => (
              <button
                key={e}
                type="button"
                className="ws-selectpill"
                aria-pressed={filters.effort.includes(e)}
                onClick={() => onFilters(toggleValue(filters, "effort", e))}
              >
                {e}
              </button>
            ))}
          </div>
        </section>

        {assignees.length > 0 && (
          <section>
            <div className="ws-section-label">Assignee</div>
            <div className="ws-pillset">
              {assignees.map((a) => (
                <button
                  key={a.label}
                  type="button"
                  className="ws-selectpill"
                  aria-pressed={filters.assignee.includes(a.label)}
                  onClick={() => onFilters(toggleValue(filters, "assignee", a.label))}
                >
                  {a.label}
                  <span className="ws-selectpill__count">{a.count}</span>
                </button>
              ))}
            </div>
          </section>
        )}

        {labels.length > 0 && (
          <section>
            <div className="ws-section-label">Tag</div>
            <div className="ws-pillset">
              {labels.map((l) => (
                <button
                  key={l.label}
                  type="button"
                  className="ws-selectpill"
                  aria-pressed={filters.labels.includes(l.label)}
                  onClick={() => onFilters(toggleValue(filters, "tag", l.label))}
                >
                  {l.label}
                  <span className="ws-selectpill__count">{l.count}</span>
                </button>
              ))}
            </div>
          </section>
        )}

        {!lockedScope && (
          <section>
            <div className="ws-section-label">Scope</div>
            <div className="ws-sheetrows">
              <button
                type="button"
                className="ws-sheetrow"
                aria-pressed={!filters.scope}
                onClick={() => onFilters({ ...filters, scope: null })}
              >
                All scopes
              </button>
              {scopes.map((s) => (
                <button
                  key={s}
                  type="button"
                  className="ws-sheetrow"
                  aria-pressed={filters.scope === s}
                  onClick={() => onFilters({ ...filters, scope: s })}
                >
                  {scopeLabel(s)}
                </button>
              ))}
            </div>
          </section>
        )}

        {/* Pills, not a Segmented. There are six sort keys and a segmented
            control divides its width equally between them, which is ~60px each
            on a 390px screen. Pills wrap. */}
        <section>
          <div className="ws-section-label">Sort by</div>
          <div className="ws-pillset">
            {TASK_SORTS.map((s) => (
              <button
                key={s.value}
                type="button"
                className="ws-selectpill"
                aria-pressed={sort.key === s.value}
                onClick={() => onSort({ ...sort, key: s.value as TaskSort })}
              >
                {s.label}
              </button>
            ))}
          </div>
          <div className="ws-pillset ws-filtersheet__dir">
            <button
              type="button"
              className="ws-selectpill"
              aria-pressed={sort.dir === "desc"}
              onClick={() => onSort({ ...sort, dir: "desc" })}
            >
              <ArrowDown aria-hidden /> Descending
            </button>
            <button
              type="button"
              className="ws-selectpill"
              aria-pressed={sort.dir === "asc"}
              onClick={() => onSort({ ...sort, dir: "asc" })}
            >
              <ArrowUp aria-hidden /> Ascending
            </button>
          </div>
        </section>
      </div>
    </Modal>
  );
}

/** The List / Board switcher, wide viewports only.
 *
 *  "Table" was the other option until the table was deleted; the list is what
 *  replaced it, and it is now the same rows the phone gets. The board stays,
 *  because it is a genuinely different read: six lanes answering "what is
 *  stuck, what is waiting, what is in flight" at a glance, which one sorted
 *  column cannot do at any length.
 *
 *  A THIRD, PERMANENTLY-DISABLED "Timeline" slot used to sit here, on the
 *  reasoning that holding the space saved redesigning the control when the view
 *  landed. It did not land, and in the meantime the page advertised a view the
 *  system does not have and could not be told when it would. A control that can
 *  never be pressed is not a roadmap, it is a dead affordance. Adding a third
 *  option to a Segmented costs one line on the day it is real. */
export function ViewSwitcher({ view, onView }: {
  view: TicketView; onView: (v: TicketView) => void;
}) {
  return (
    <Segmented
      ariaLabel="Ticket view"
      value={view}
      onChange={(v) => onView(v as TicketView)}
      options={[
        { value: "list", label: "List" },
        { value: "board", label: "Board" },
      ]}
      hint={undefined}
    />
  );
}
