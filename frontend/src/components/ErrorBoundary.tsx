import { RotateCw, TriangleAlert } from "lucide-react";
import { Component, type ReactNode } from "react";
import i18n from "../i18n";
import { Button, EmptyState } from "./ui";

const RELOADED = "plainboard.reloadedForUpdate";

/** A part that is loaded when it is first opened (settings, docs, …) is gone after an update while the app stays
 * open (a phone keeps it for days): loading the new version once is the fix. */
const isStaleBuild = (error: unknown) =>
  error instanceof Error && /dynamically imported module|Importing a module script failed|error loading dynamically imported|Unable to preload/i.test(error.message);

/** What breaks while showing the app shows as a message with a way back, not as an empty (black) screen. */
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    if (!isStaleBuild(error)) return;
    const last = Number(sessionStorage.getItem(RELOADED) || 0);
    if (Date.now() - last < 60_000) return; // reloaded a moment ago: the message stays instead of a loop
    sessionStorage.setItem(RELOADED, String(Date.now()));
    window.location.reload();
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div role="alert" className="flex h-full items-center justify-center bg-bg pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)]">
        <EmptyState icon={<TriangleAlert className="size-10" />} title={i18n.t(isStaleBuild(error) ? "app.updated" : "app.crashed")}>
          <p className="mb-4 break-words">{error.message}</p>
          <Button variant="primary" icon={<RotateCw className="size-4" />} onClick={() => window.location.reload()}>
            {i18n.t("app.reload")}
          </Button>
        </EmptyState>
      </div>
    );
  }
}
