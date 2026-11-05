import { useCallback, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "../../api/schema.gen";
import { getJson } from "../../lib/http";
import { queryKeys } from "../../lib/queryKeys";

type Layout = components["schemas"]["Layout"];

const LAYOUT = "/api/dashboard/layout";

/** The operator's saved app order, the one the Apps card is arranged in. The
 *  dock and the Apps page read it too, so an app is in one place everywhere. */
export function useSavedOrder(): string[] {
  const q = useQuery({ queryKey: queryKeys.dashboard.layout(), queryFn: () => getJson<Layout>(LAYOUT) });
  return q.data?.order ?? [];
}

export type Arrange = ReturnType<typeof useArrange>;

/** The Apps card's saved order and its arrange mode: while arranging, tiles are
 *  dragged into a new order, kept on the server. Escape ends arranging. */
export function useArrange() {
  const qc = useQueryClient();
  const layoutQ = useQuery({ queryKey: queryKeys.dashboard.layout(), queryFn: () => getJson<Layout>(LAYOUT) });
  const [arranging, setArranging] = useState(false);

  const start = useCallback(() => setArranging(true), []);
  const stop = useCallback(() => setArranging(false), []);

  const reorder = useCallback(async (order: string[]) => {
    const prev = layoutQ.data;
    if (prev) qc.setQueryData(queryKeys.dashboard.layout(), { ...prev, order });
    try {
      const res = await fetch(LAYOUT, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ order }),
      });
      if (res.ok) qc.setQueryData(queryKeys.dashboard.layout(), (await res.json()) as Layout);
    } catch {
      // The optimistic order stays; the next read brings the server's back.
    }
  }, [qc, layoutQ.data]);

  useEffect(() => {
    if (!arranging) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") stop(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [arranging, stop]);

  return { order: layoutQ.data?.order ?? [], arranging, start, stop, reorder };
}
