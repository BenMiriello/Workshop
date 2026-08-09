import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { RefreshCw, Gauge, SlidersHorizontal, FolderCog } from "lucide-react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  Button,
  Card,
  CardGrid,
  ChartFrame,
  ChartTooltip,
  CHART_SERIES,
  Field,
  Input,
  Modal,
  PageHeader,
  SectionLabel,
  Segmented,
  Skeleton,
  Stack,
  StackedBar,
  Stat,
  StateBlock,
  Table,
  TableScroll,
  NumCell,
  useEditMode,
  type SegmentedOption,
} from "../ui";
import {
  usageApi,
  fmtTokens,
  type UsageStatus,
  type UsageSeries,
  type UsageWindow,
} from "../lib/usage";

// The Usage page. Reads GET /api/usage (services.usage_ledger.status) for a
// selectable window: 5h / 24h / 7d / 30d / all time.
//
// Only the 5h window is the BUDGET window — the cap is defined as a rolling 5h
// ceiling, so it is meaningless against a 7-day total. Every longer lookback is
// reporting-only (`capped: false`), and the page changes shape accordingly:
// allowance/remaining disappear and the bar reads as share-of-total rather than
// a fraction of a cap. One bar on the page, below the chart: a single track
// segmented by source, largest first.
//
// Chat turns ARE metered (role="chat"), reported but never counted against the
// cap — admission already reserves interactive headroom, so gating on chat spend
// too would double-count that reserve.

const ENDPOINT = "/api/usage";

const WINDOW_OPTIONS: SegmentedOption<UsageWindow>[] = [
  { value: "5h", label: "5h" },
  { value: "24h", label: "24h" },
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
  { value: "all", label: "All time" },
];

// The subtitle has to carry the one thing that changes meaning with the window:
// only 5h is the BUDGET window. Every longer lookback is reporting-only, so it
// must not be described in terms of a cap the operator would then read the bar
// against.
function windowLabel(data: UsageStatus, win: UsageWindow): string {
  if (data.capped) {
    const hours = Math.round(data.window_s / 3600);
    return `Rolling ${hours}-hour budget window`;
  }
  if (win === "all") {
    const days = Math.max(1, Math.round(data.window_s / 86400));
    return `All recorded usage (~${days} day${days === 1 ? "" : "s"} so far). Reporting only, no cap.`;
  }
  return `Trailing ${WINDOW_OPTIONS.find((o) => o.value === win)?.label ?? ""}. Reporting only, no cap.`;
}

// X-axis tick: an ISO timestamp -> a short local clock time (e.g. "3:40 PM").
function clockTick(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

type Row = {
  slug: string;
  used: number;
  // null on an uncapped window — no allowance exists to report there.
  allowance: number | null;
  remaining: number | null;
  share: number; // 0..1
};

export default function Usage() {
  const nav = useNavigate();
  const [data, setData] = useState<UsageStatus | null>(null);
  const [series, setSeries] = useState<UsageSeries | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Which lookback we are reporting. A view preference, so it commits on change
  // (design system: only config/money writes wait for an explicit Save).
  const [win, setWin] = useState<UsageWindow>("5h");

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const [status, ts] = await Promise.all([usageApi.status(win), usageApi.timeseries(win)]);
      setData(status);
      setSeries(ts);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [win]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 30_000);
    return () => clearInterval(t);
  }, [refresh]);

  const rows = useMemo<Row[]>(() => {
    if (!data) return [];
    return Object.entries(data.projects)
      .map(([slug, p]) => ({
        slug,
        used: p.used,
        allowance: p.allowance,
        remaining: p.remaining,
        share: p.share_of_app,
      }))
      .sort((a, b) => b.used - a.used);
  }, [data]);

  // Edit mode for the two operator-tunable budget knobs. Config write, so it
  // commits on explicit Save (not on change), per the design system.
  const edit = useEditMode(
    { app_total: data?.app_total ?? 0 },
    async (draft) => {
      const next = await usageApi.setBudget({ app_total: Math.round(draft.app_total) });
      setData(next);
    },
  );

  const header = (
    <PageHeader
      title="Usage"
      sub={data ? windowLabel(data, win) : undefined}
      actions={
        <>
          <Segmented<UsageWindow>
            ariaLabel="Usage window"
            value={win}
            onChange={setWin}
            options={WINDOW_OPTIONS}
          />
          <Button size="sm" variant="ghost" onClick={() => nav("/projects")}>
            <FolderCog aria-hidden /> Manage projects
          </Button>
          <Button size="sm" onClick={refresh} loading={loading}>
            <RefreshCw aria-hidden /> Refresh
          </Button>
        </>
      }
    />
  );

  let body: JSX.Element | null = null;
  if (err) {
    body = (
      <StateBlock
        variant="error"
        title="Couldn't load usage"
        hint={
          <>
            <code>{ENDPOINT}</code> ({err})
          </>
        }
        action={
          <Button size="sm" onClick={refresh}>
            Retry
          </Button>
        }
      />
    );
  } else if (!data) {
    body = (
      <Stack gap="lg">
        <CardGrid>
          {[0, 1, 2].map((i) => (
            <Card key={i}>
              <Stack gap="sm">
                <Skeleton width="45%" />
                <Skeleton width="60%" />
              </Stack>
            </Card>
          ))}
        </CardGrid>
        <Card>
          <Stack gap="sm">
            <Skeleton width="30%" />
            <Skeleton width="90%" />
            <Skeleton width="80%" />
          </Stack>
        </Card>
      </Stack>
    );
  } else {
    body = (
      <Stack gap="lg">
        <section>
          <Stack gap="sm">
            <SectionLabel icon={<Gauge aria-hidden size={14} />}>
              App-wide window
              {/* The cap is a property of the 5h budget window only, so the
                  control to edit it appears only there — offering it on a 30-day
                  view would imply that view is bounded by it. */}
              {data.capped && !edit.editing && (
                <Button size="sm" variant="ghost" onClick={edit.begin} style={{ marginLeft: "auto" }}>
                  <SlidersHorizontal aria-hidden /> Edit cap
                </Button>
              )}
            </SectionLabel>

            <div className="ws-stat-strip">
              <Stat label="Used" value={fmtTokens(data.app_used)} sub="tokens this window" />
              {data.capped && data.app_remaining != null && (
                <Stat label="Remaining" value={fmtTokens(data.app_remaining)} sub="until the cap" />
              )}
              {data.capped && data.app_total != null ? (
                <Stat label="Total cap" value={fmtTokens(data.app_total)} sub="per window" />
              ) : (
                <Stat label="Sources" value={String(rows.length)} sub="apps with usage" />
              )}
            </div>

            {series && series.points.length > 0 && series.projects.length > 0 && (
              <ChartFrame style={{ width: "100%", height: 135 }}>
                <ResponsiveContainer>
                  <LineChart data={series.points} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                    <XAxis
                      dataKey="t"
                      tickFormatter={clockTick}
                      tick={{ fontSize: 11, fill: "var(--muted)" }}
                      minTickGap={40}
                    />
                    <YAxis
                      tickFormatter={(v) => fmtTokens(v as number)}
                      tick={{ fontSize: 11, fill: "var(--muted)" }}
                      width={44}
                    />
                    <Tooltip
                      isAnimationActive={false}
                      cursor={{ stroke: "var(--border)" }}
                      content={({ active, payload, label }: any) => (
                        <ChartTooltip
                          active={active}
                          label={label ? clockTick(label as string) : ""}
                          showName
                          payload={payload ?? []}
                          format={(v) => fmtTokens(v as number)}
                        />
                      )}
                    />
                    {series.projects.map((slug, i) => (
                      <Line
                        key={slug}
                        type="monotone"
                        dataKey={slug}
                        name={slug}
                        stroke={CHART_SERIES[i % CHART_SERIES.length]}
                        strokeWidth={2}
                        dot={false}
                        isAnimationActive={false}
                      />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              </ChartFrame>
            )}

            {/* The one bar on this page, below the chart: a single track split by
                app, largest first. When the window is capped the segments are
                drawn against the cap so the bare remainder is real headroom;
                uncapped, they fill the width and the bar reads as share of
                total — the only honest shape when there is no limit to be a
                fraction OF. */}
            {rows.length > 0 && (
              <StackedBar
                segments={rows.map((r, i) => ({
                  key: r.slug,
                  value: r.used,
                  color: CHART_SERIES[i % CHART_SERIES.length],
                }))}
                max={data.capped && data.app_total != null ? data.app_total : undefined}
                label={
                  <>
                    <span>
                      {data.capped && data.app_total != null
                        ? `${fmtTokens(data.app_used)} of ${fmtTokens(data.app_total)} used this window`
                        : `${fmtTokens(data.app_used)} total across ${rows.length} ${rows.length === 1 ? "source" : "sources"}`}
                    </span>
                    <span>
                      {data.capped && data.app_total != null && data.app_total > 0
                        ? `${Math.round((data.app_used / data.app_total) * 100)}%`
                        : "no cap"}
                    </span>
                  </>
                }
              />
            )}
          </Stack>
        </section>

        <Modal
          open={edit.editing}
          onClose={edit.cancel}
          title="Edit token cap"
          actions={
            <>
              <Button size="sm" variant="ghost" onClick={edit.cancel}>
                Cancel
              </Button>
              <Button size="sm" onClick={edit.save} loading={edit.saving} disabled={!edit.dirty}>
                Save
              </Button>
            </>
          }
        >
          <Field
            label="App-wide token cap"
            htmlFor="cap"
            help="The most autonomous work may spend per rolling 5-hour window."
          >
            <Input
              id="cap"
              data-autofocus
              type="number"
              min={1}
              step={100000}
              value={edit.draft.app_total}
              onChange={(e) => edit.set("app_total", Number(e.target.value) || 0)}
            />
          </Field>
        </Modal>

        <section>
          {rows.length === 0 ? (
            <StateBlock
              title="No usage in this window"
              hint="Nothing has been billed in this window. Try a longer one."
            />
          ) : (
            <Stack gap="lg">

              <TableScroll>
                <Table>
                  <thead>
                    <tr>
                      <th>Source</th>
                      <th style={{ textAlign: "right" }}>Used</th>
                      {/* Allowance/Remaining are 5h-budget concepts; on a longer
                          lookback there is nothing to report, so the columns go
                          away rather than render a blank or a borrowed number. */}
                      {data.capped && (
                        <>
                          <th className="ws-col-optional" style={{ textAlign: "right" }}>Allowance</th>
                          <th className="ws-col-optional" style={{ textAlign: "right" }}>Remaining</th>
                        </>
                      )}
                      <th style={{ textAlign: "right" }}>Share</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.slug}>
                        <td>
                          <Link
                            to={`/projects/${r.slug}`}
                            style={{ color: "var(--accent)", fontWeight: "var(--fw-medium)" }}
                          >
                            {r.slug}
                          </Link>
                        </td>
                        <NumCell>{fmtTokens(r.used)}</NumCell>
                        {data.capped && (
                          <>
                            <NumCell className="ws-col-optional">
                              {r.allowance != null ? fmtTokens(r.allowance) : "—"}
                            </NumCell>
                            <NumCell className="ws-col-optional">
                              {r.remaining != null ? fmtTokens(r.remaining) : "—"}
                            </NumCell>
                          </>
                        )}
                        <NumCell>{Math.round(r.share * 100)}%</NumCell>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </TableScroll>
            </Stack>
          )}
        </section>
      </Stack>
    );
  }

  return (
    <div>
      {header}
      {body}
    </div>
  );
}
