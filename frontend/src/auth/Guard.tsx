// Auth gate. While the stored session is being resolved it shows a minimal
// brand splash; with no session it shows the login screen; once authenticated
// it renders the app. Wrapping the app means none of its data-fetching effects
// run until there's a session to authorize them.

import { type ReactNode } from "react";
import { Hexagon } from "lucide-react";

import { useAuth } from "./AuthProvider";
import Login from "../pages/Login";

export function Guard({ children }: { children: ReactNode }) {
  const { status } = useAuth();

  if (status === "loading") {
    return (
      <div className="ws-authscreen">
        <div className="ws-authscreen__brand">
          <Hexagon size={20} strokeWidth={1.8} aria-hidden />
          <span>Workshop</span>
        </div>
      </div>
    );
  }

  if (status === "anon") return <Login />;

  return <>{children}</>;
}
