// The filter row and its menus, and the phone's filter sheet. Both drive the
// same `TicketFilters` value; neither owns any filtering logic of its own (that
// is lib/ticketFilters.ts, which is pure and tested).
//
// The "+ Filter" menu is ONE popover grouped by field rather than a field list
// that opens a second popover per field. Two levels of popover is a fragile
// shape (two placements to flip, two dismiss rules, a submenu that has to
// survive the pointer leaving it) and it costs an extra interaction on the most
// common action there is here, which is ticking two statuses.
import { Plus, Search, SlidersHorizontal, ArrowDown, ArrowUp } from "lucide-react";
import {
  Button, FilterBar, FilterChip, Menu, MenuCheckItem, MenuItem, MenuLabel, Modal,
  Segmented, StatusDot,
} from "@workshop/ui";
import {
  TASK_STATUSES, TASK_SORTS, TASK_EFFORTS, TASK_SOURCES, scopeLabel, sourceLabel,
  taskStatusKind, taskStatusLabel, taskStatusTitle, type TaskSort,
} from "../../lib/tasks";
import {
  chipsFor, clearField, toggleValue,
  type TicketFilters as Filters, type TicketSort, type TicketView,
} from "../../lib/ticketFilters";

export type LabelCount = { label: string; count: number };

type Props = {
  filters: Filters;
  sort: TicketSort;
  view: TicketView;
  scopes: string[];
  labels: LabelCount[];
  assignees: LabelCount[];
  /** null when the surface does not know its total (see the board). */
  count: number | null;
  onFilters: (next: Filters) => void;
  onSort: (next: TicketSort) => void;
  onNew: () => void;
};

// The field menu, shared by the desktop bar and (as a flat body) by the sheet.
function FieldMenuBody({ filters, scopes, labels, assignees, onFilters }: {
  filters: Filters; scopes: string[]; labels: LabelCount[]; assignees: LabelCount[];
  onFilters: (next: Filters) => void;
}) {
  return (
    <>
      <MenuLabel>Status</MenuLabel>
      {TASK_STATUSES.map((s) => (
        <MenuCheckItem
          key={s}
          checked={filters.status.includes(s)}
          onToggle={() => onFilters(toggleValue(filters, "status", s))}
        >
          {taskStatusLabel(s)}
        </MenuCheckItem>
      ))}
      {/* WHERE IT CAME FROM. Second, under Status, because on this queue it is
          the strongest narrowing there is: 132 of 208 open tickets were filed by
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
  filters, sort, view, scopes, labels, assignees, count, onFilters, onSort, onNew,
}: Props) {
  const chips = chipsFor(filters, view, {
    scope: scopeLabel,
    status: taskStatusLabel,
    source: sourceLabel,
  });

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
  filters, sort, view, onOpen, onFilters,
}: {
  filters: Filters; sort: TicketSort; view: TicketView;
  onOpen: () => void; onFilters: (next: Filters) => void;
}) {
  const chips = chipsFor(filters, view, {
    scope: scopeLabel, status: taskStatusLabel, source: sourceLabel,
  });
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
  open, onClose, filters, sort, scopes, labels, assignees, count,
  onFilters, onSort, onReset,
}: {
  open: boolean; onClose: () => void;
  filters: Filters; sort: TicketSort; scopes: string[]; labels: LabelCount[];
  assignees: LabelCount[];
  /** null when the surface does not know its total (see the board). */
  count: number | null;
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
        <section>
          <div className="ws-section-label">Status</div>
          <div className="ws-pillset">
            {TASK_STATUSES.map((s) => {
              const on = filters.status.includes(s);
              return (
                <button
                  key={s}
                  type="button"
                  className="ws-selectpill"
                  aria-pressed={on}
                  onClick={() => onFilters(toggleValue(filters, "status", s))}
                >
                  <StatusDot status={taskStatusKind(s)}>{null}</StatusDot>
                  {taskStatusTitle(s)}
                </button>
              );
            })}
          </div>
        </section>

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

/** The Table / Board switcher.
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
        { value: "table", label: "Table" },
        { value: "board", label: "Board" },
      ]}
      hint={undefined}
    />
  );
}
