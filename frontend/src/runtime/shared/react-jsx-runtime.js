// Shell-published runtime for `react/jsx-runtime`. See ./guard for why this file exists:
// it is a stable URL for the ONE instance the shell already bundles, so an
// app's bundle can import the same one instead of shipping its own.
import "./guard";
export * from "react/jsx-runtime";
