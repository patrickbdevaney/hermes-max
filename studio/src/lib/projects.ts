// Project state — typed wrappers over the Rust project commands (projects.rs).
// A "project" is a working directory + a name + run history; the user never
// hears "working directory" or "cwd".
import { invoke } from "./tauri";

export interface Project {
  id: string;
  name: string;
  dir: string;                 // the actual build directory (never shown raw to users)
  prompt?: string | null;      // most recent prompt
  created_ts: number;
  last_run_ts?: number | null;
  last_status?: string | null; // "ready" | "building" | "done" | "attention"
  last_step?: number | null;
  last_total?: number | null;
  lifetime_cost_usd?: number;
  lifetime_tokens?: number;
}

export const listProjects = () => invoke<Project[]>("list_projects");
export const createProject = (name: string, dir: string | null, newFolder: boolean) =>
  invoke<Project>("create_project", { name, dir, newFolder });
export const renameProject = (id: string, name: string) =>
  invoke<Project>("rename_project", { id, name });
export const deleteProject = (id: string) => invoke("delete_project", { id });
export const openProjectFolder = (dir: string) => invoke("open_path", { path: dir });
export const pickDirectory = () => invoke<string | null>("pick_directory");
