// The chat scope a conversation beside this page is filed under: the app's own
// chat when it declares one (`app:<slug>`), else the general one.

import { useQuery } from "@tanstack/react-query";
import { chatApi } from "../../../lib/chat";
import { queryKeys } from "../../../lib/queryKeys";

export function usePageScope(app: string | null): string {
  const scopes = useQuery({
    queryKey: queryKeys.chat.providerKeys(),
    queryFn: () => chatApi.listProviders().then((list) => list.map((p) => p.key)),
    retry: false,
    staleTime: 60_000,
  });
  const own = app ? `app:${app}` : null;
  return own && scopes.data?.includes(own) ? own : "general";
}
