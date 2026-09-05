import type { UserConfig } from "vite";

/** Every specifier the shell publishes at runtime: the externals of every app
 *  build and the keys of the shell's import map, as one array. */
export declare const SHARED: readonly string[];

export interface WorkshopAppBuildOptions {
  /** The app directory. Defaults to the cwd of the build. */
  root?: string;
  /** The app's ESM entry. Defaults to `ui/index.ts`. */
  entry?: string;
  /** Where the built bundle goes, relative to `root`. Defaults to `dist/ui`,
   *  which is what the manifest's `ui.dir` names. */
  outDir?: string;
  /** A second, tiny entry exporting `{ Provider }` for an app needing a
   *  session-scoped context. Awaited before the routed area first renders. */
  providerEntry?: string;
}

export declare function defineWorkshopApp(options?: WorkshopAppBuildOptions): UserConfig;
export default defineWorkshopApp;
