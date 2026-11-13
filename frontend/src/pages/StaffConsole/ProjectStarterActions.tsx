import { PROJECT_WORKFLOWS } from "./projectWorkflows";
import "./projectStarterActions.css";

/** Stage a project prompt in the current composer; selection and sending stay with the caller. */
export function ProjectStarterActions({ onPrefill }: { onPrefill: (prompt: string) => void }) {
  return (
    <nav className="project-starter-actions" aria-label="Project starters">
      {PROJECT_WORKFLOWS.map(({ project, prompt }) => (
        <button key={project} type="button" aria-label={`Start ${project}`} onClick={() => onPrefill(prompt)}>
          Start {project}
        </button>
      ))}
    </nav>
  );
}
