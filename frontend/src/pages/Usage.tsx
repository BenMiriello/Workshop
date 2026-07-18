import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { RefreshCw, Gauge, FolderGit2, SlidersHorizontal, FolderCog } from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  ActionBar,
  Button,
  Card,
  CardGrid,
  ChartFrame,
  ChartTooltip,
  CHART_SERIES,
  Field,
  Input,
  PageHeader,
  ProgressBar,
  SectionLabel,
  Skeleton,
  Stack,
  Stat,
  StateBlock,
  Table,
  TableScroll,
  NumCell,
  useEditMode,
  type StatusKind,
} from "../ui";
import { usageApi, fmtTokens, type UsageStatus, type UsageSeries } from "../lib/usage";

// The Usage page: the rolling-window token budget for autonomous background
// work. Reads GET /api/usage (services.usage_ledger.status). Shows the app-wide
// window (used / remaining / total) plus a per-project breakdown (used,
// allowance, remaining, share). Interactive chat turns are never metered, so
// they never appear here; only autonomous sweeps write tokens.

const ENDPOINT = "/api/usage";

// App-wide headroom -> one of the five status kinds. Green while there is room,
// amber past 70% of the window cap, red past 90%.
function headroomKind(used: number, total: number): StatusKind {
  if (total <= 0) return "ok";
  const frac = used / total;
  if (frac >= 0.9) return "danger";
  if (frac >= 0.7) return "warn";
  return "ok";
}

function windowLabel(windowS: number): string {
  const hours = Math.round(windowS / 3600);
  return `Rolling ${hours}-hour window`;
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
  allowance: number;
  remaining: number;
  share: number; // 0..1
};

export default function Usage() {
  const nav = useNavigate();
  const [data, setData] = useState<UsageStatus | null>(null);
  const [series, setSeries] = useState<UsageSeries | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const [status, ts] = await Promise.all([usageApi.status(), usageApi.timeseries()]);
      setData(status);
      setSeries(ts);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

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
      sub={data ? windowLabel(data.window_s) : undefined}
      actions={
        <>
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
    const kind = headroomKind(data.app_used, data.app_total);
    body = (
      <Stack gap="lg">
        <section>
          <Stack gap="sm">
            <SectionLabel icon={<Gauge aria-hidden size={14} />}>
              App-wide window
              {!edit.editing && (
                <Button size="sm" variant="ghost" onClick={edit.begin} style={{ marginLeft: "auto" }}>
                  <SlidersHorizontal aria-hidden /> Edit cap
                </Button>
              )}
            </SectionLabel>

            <div className="ws-stat-strip">
              <Stat label="Used" value={fmtTokens(data.app_used)} sub="tokens this window" />
              <Stat label="Remaining" value={fmtTokens(data.app_remaining)} sub="until the cap" />
              {edit.editing ? (
                <Field label="Total cap" htmlFor="cap">
                  <Input
                    id="cap"
                    type="number"
                    min={1}
                    step={100000}
                    value={edit.draft.app_total}
                    onChange={(e) => edit.set("app_total", Number(e.target.value) || 0)}
                  />
                </Field>
              ) : (
                <Stat label="Total cap" value={fmtTokens(data.app_total)} sub="per window" />
              )}
            </div>
            {edit.editing && (
              <ActionBar flash={edit.flash ? "Saved" : undefined}>
                <Button size="sm" onClick={edit.save} loading={edit.saving} disabled={!edit.dirty}>
                  Save
                </Button>
                <Button size="sm" variant="ghost" onClick={edit.cancel}>
                  Cancel
                </Button>
              </ActionBar>
            )}

            <ProgressBar
              value={data.app_used}
              max={data.app_total}
              status={kind}
              label={
                <>
                  <span>
                    {fmtTokens(data.app_used)} of {fmtTokens(data.app_total)} used this window
                  </span>
                  <span>
                    {data.app_total > 0
                      ? `${Math.round((data.app_used / data.app_total) * 100)}%`
                      : "uncapped"}
                  </span>
                </>
              }
            />

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
          </Stack>
        </section>

        <section>
          <SectionLabel icon={<FolderGit2 aria-hidden size={14} />}>Per project</SectionLabel>
          {rows.length === 0 ? (
            <StateBlock
              title="No autonomous usage yet"
              hint={
                <>
                  Nothing has been billed to a project in this window. Autonomous sweeps append to{" "}
                  <code>.runtime/usage.jsonl</code> (via <code>cron_agent.sh</code>, a project or
                  agent Ralph sweep, or a drained event). Interactive chat is exempt, so it never
                  appears here.
                </>
              }
            />
          ) : (
            <Stack gap="lg">
              <ChartFrame style={{ width: "100%", height: Math.max(140, rows.length * 44 + 40) }}>
                <ResponsiveContainer>
                  <BarChart
                    layout="vertical"
                    data={rows}
                    margin={{ top: 8, right: 8, left: 8, bottom: 0 }}
                    barCategoryGap="30%"
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" horizontal={false} />
                    <XAxis
                      type="number"
                      tickFormatter={(v) => fmtTokens(v as number)}
                      tick={{ fontSize: 11, fill: "var(--muted)" }}
                    />
                    <YAxis
                      type="category"
                      dataKey="slug"
                      width={110}
                      tick={{ fontSize: 11, fill: "var(--muted)" }}
                    />
                    <Tooltip
                      isAnimationActive={false}
                      cursor={{ fill: "var(--border)", fillOpacity: 0.25 }}
                      content={({ active, payload, label }: any) => (
                        <ChartTooltip
                          active={active}
                          label={label}
                          showName
                          payload={payload ?? []}
                          format={(v) => fmtTokens(v as number)}
                        />
                      )}
                    />
                    <Bar
                      dataKey="used"
                      name="Used"
                      stackId="a"
                      fill={CHART_SERIES[0]}
                      isAnimationActive={false}
                    />
                    <Bar
                      dataKey="remaining"
                      name="Remaining"
                      stackId="a"
                      fill={CHART_SERIES[1]}
                      isAnimationActive={false}
                      radius={[0, 3, 3, 0]}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </ChartFrame>

              <TableScroll>
                <Table>
                  <thead>
                    <tr>
                      <th>Project</th>
                      <th style={{ textAlign: "right" }}>Used</th>
                      <th className="ws-col-optional" style={{ textAlign: "right" }}>Allowance</th>
                      <th className="ws-col-optional" style={{ textAlign: "right" }}>Remaining</th>
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
                        <NumCell className="ws-col-optional">{fmtTokens(r.allowance)}</NumCell>
                        <NumCell className="ws-col-optional">{fmtTokens(r.remaining)}</NumCell>
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
