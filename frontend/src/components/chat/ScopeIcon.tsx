import { Blocks, Bot, CodeXml, FolderGit2, Home } from "lucide-react";
import { type ScopeKind } from "./chatScope";

// One icon per scope KIND, not per scope: the icon says what kind of place a
// row is, and the name says which one. Sixteen project rows all carrying the
// same folder is the point — that is how you see the group at a glance.
//
// Lives on its own because two surfaces draw it: the toolbar's scope picker,
// and the recent-chats menu, where it says which place each conversation is in.
// It was private to the toolbar; importing it back out of that file would have
// made a cycle.
export default function ScopeIcon({ kind }: { kind: ScopeKind }) {
  // Development is the source of the Workshop itself, so it takes the code
  // icon rather than a second house — the point of the row is that it is a
  // different KIND of place from the root scope, not another root.
  if (kind === "development") return <CodeXml size={14} aria-hidden />;
  if (kind === "project") return <FolderGit2 size={14} aria-hidden />;
  if (kind === "app") return <Blocks size={14} aria-hidden />;
  if (kind === "agent") return <Bot size={14} aria-hidden />;
  return <Home size={14} aria-hidden />;
}
