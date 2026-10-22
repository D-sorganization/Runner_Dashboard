/**
 * Fleet skills catalog and tracked projects (#2157 slice 7).
 */
import React, { useEffect, useState } from "react";
import { SkeletonLine } from "../../primitives/Skeleton";
import { fetchProjectList, fetchSkillCatalog, type StaffProjectSummary, type StaffSkillSummary } from "./fleetCatalogApi";

export type FleetCatalogView = "skills" | "projects";

export interface FleetCatalogPaneProps {
  view: FleetCatalogView;
}

export const FleetCatalogPane: React.FC<FleetCatalogPaneProps> = ({ view }) => {
  const [skills, setSkills] = useState<StaffSkillSummary[] | null>(null);
  const [projects, setProjects] = useState<StaffProjectSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    const load = async () => {
      try {
        if (view === "skills") {
          setSkills(null);
          const rows = await fetchSkillCatalog();
          if (!cancelled) setSkills(rows);
        } else {
          setProjects(null);
          const rows = await fetchProjectList();
          if (!cancelled) setProjects(rows);
        }
      } catch (err: unknown) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load");
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [view]);

  return (
    <div className="fleet-catalog" data-testid="fleet-catalog-pane">
      {error && (
        <div role="alert" className="context-error-alert">
          {error}
        </div>
      )}
      {view === "skills" && (
        <ul className="fleet-catalog__list">
          {skills === null && !error && (
            <li>
              <SkeletonLine height={14} width="80%" />
            </li>
          )}
          {skills?.map((skill) => (
            <li key={skill.pin} className="fleet-catalog__item">
              <strong>{skill.name}</strong>
              <span className="fleet-catalog__meta">{skill.version}</span>
              <p>{skill.description || skill.when_to_use}</p>
            </li>
          ))}
          {skills && skills.length === 0 && <li>No skills indexed yet.</li>}
        </ul>
      )}
      {view === "projects" && (
        <ul className="fleet-catalog__list">
          {projects === null && !error && (
            <li>
              <SkeletonLine height={14} width="70%" />
            </li>
          )}
          {projects?.map((project) => (
            <li key={project.id} className="fleet-catalog__item">
              <strong>{project.title}</strong>
              <span className="fleet-catalog__meta">{project.status}</span>
              <p>{project.goal}</p>
              {project.tasks.length > 0 && (
                <p className="fleet-catalog__meta">{project.tasks.length} planned task(s)</p>
              )}
            </li>
          ))}
          {projects && projects.length === 0 && <li>No projects yet.</li>}
        </ul>
      )}
    </div>
  );
};
