import { Card, PageHeader, StateBlock } from "../../../frontend/src/ui";

// Entry component for the {{title}} sub-app. Replace this scaffold with
// real content. Mount via:
//   frontend/src/App.tsx → <Route path="/apps/{{slug}}" element={<App />} />
// and add a card in frontend/src/pages/Apps.tsx pointing here.
export default function App() {
  return (
    <div>
      <PageHeader title="{{title}}" sub="{{purpose}}" />
      <Card style={{ padding: "var(--space-5)" }}>
        <StateBlock
          title="Scaffold ready"
          hint="Tell the build agent what to wire up here."
        />
      </Card>
    </div>
  );
}
