import { Link, useLocation } from "react-router-dom";
import { FileQuestion } from "lucide-react";
import { PageHeader, StateBlock } from "@workshop/ui";

// A URL THIS APP DOES NOT HAVE SAYS SO.
//
// There was no catch-all, so every unmatched path rendered the shell with an
// empty main: the dock, the sidebar, and a blank column. That is the same
// picture a page whose data failed to load produces, and the same picture a
// broken build produces, so an ordinary typo or a stale bookmark was
// indistinguishable from the app being broken. Reported 2026-08-29 from
// `/chat`, which does not exist; the chat list is `/chats`.
//
// It names the path, because the whole question the reader has is which URL
// they are on and whether they mistyped it.
export default function NotFound() {
  const { pathname } = useLocation();
  return (
    <>
      <PageHeader title="Not found" />
      <StateBlock
        icon={<FileQuestion aria-hidden />}
        title="This page does not exist"
        hint={<code>{pathname}</code>}
        action={<Link to="/">Go home</Link>}
      />
    </>
  );
}
