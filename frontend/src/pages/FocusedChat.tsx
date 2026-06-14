import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { PageHeader } from "../ui";
import Chat from "../components/Chat";
import { chatApi, type Provider } from "../lib/chat";

// Focused-chat route. Mounts Chat in "full" variant so the operator can pop a
// build/data conversation out of its host context and treat it like a normal
// LLM page.
export default function FocusedChat() {
  const { contextKey: raw = "" } = useParams();
  const contextKey = decodeURIComponent(raw);
  const [providers, setProviders] = useState<Provider[]>([]);

  useEffect(() => {
    let cancelled = false;
    chatApi
      .listProviders()
      .then((list) => { if (!cancelled) setProviders(list); })
      .catch(() => { /* the page still works without provider metadata */ });
    return () => { cancelled = true; };
  }, []);

  const label = providers.find((p) => p.key === contextKey)?.label;
  const titleText = label ? `Chat · ${label}` : `Chat · ${contextKey}`;

  return (
    <div>
      <PageHeader title={titleText} sub={<code className="ws-mono">{contextKey}</code>} />
      <Chat contextKey={contextKey} variant="full" />
    </div>
  );
}
