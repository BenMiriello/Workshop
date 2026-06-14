// DEPRECATED — use ./Chat instead. This file is a shim so existing imports
// (e.g. `import BuildChatPanel from "../components/BuildChatPanel"`) keep
// working while callers migrate.
import Chat, { type ChatProps } from "./Chat";

type Props = Pick<ChatProps, "contextKey" | "placeholder">;

export default function BuildChatPanel(props: Props) {
  return <Chat {...props} variant="panel" mode="build" />;
}
