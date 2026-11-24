"use client";

import type { ReactNode } from "react";

export type HighlightMap = Record<string, "after" | "before">;

type Props = {
  content: any;
  highlights?: HighlightMap;
  emptyLabel?: string;
};

function Marked({
  value,
  highlights,
}: {
  value?: string | null;
  highlights?: HighlightMap;
}) {
  if (!value) return null;
  const state = highlights?.[value];
  if (!state) return <>{value}</>;
  return <mark className={`hl ${state === "before" ? "before" : ""}`}>{value}</mark>;
}

function hasItems(value: unknown): value is unknown[] {
  return Array.isArray(value) && value.length > 0;
}

function dateRange(start?: string, end?: string, current?: boolean) {
  const tail = current ? "Present" : end || "";
  if (!start && !tail) return "";
  return [start, tail].filter(Boolean).join(" – ");
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="paper-section">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

export default function ResumeDocument({ content, highlights, emptyLabel }: Props) {
  if (!content) {
    return (
      <div className="paper">
        <p style={{ color: "var(--muted)", fontSize: 12 }}>{emptyLabel || "No resume content available."}</p>
      </div>
    );
  }

  const p = content.personal_information || {};
  const contact = [p.email, p.phone, p.location, ...(Array.isArray(p.links) ? p.links : [])].filter(Boolean);

  return (
    <article className="paper" aria-label="Resume preview">
      <header>
        <h1 className="paper-name">
          <Marked value={p.full_name || "Your Name"} highlights={highlights} />
        </h1>
        {p.headline && (
          <p className="paper-headline">
            <Marked value={p.headline} highlights={highlights} />
          </p>
        )}
        {contact.length > 0 && <p className="paper-contact">{contact.join("  ·  ")}</p>}
      </header>

      {content.professional_summary && (
        <Section title="Professional Summary">
          <p>
            <Marked value={content.professional_summary} highlights={highlights} />
          </p>
        </Section>
      )}

      {hasItems(content.experience) && (
        <Section title="Experience">
          {content.experience.map((exp: any, index: number) => (
            <div className="paper-entry" key={`exp-${index}`}>
              <div className="paper-entry-head">
                <strong>
                  <Marked value={exp.role} highlights={highlights} />
                  {exp.role && exp.company ? " · " : ""}
                  <Marked value={exp.company} highlights={highlights} />
                </strong>
                <em>{dateRange(exp.start_date, exp.end_date, exp.current)}</em>
              </div>
              {exp.location && <p className="paper-sub">{exp.location}</p>}
              {hasItems(exp.bullets) && (
                <ul>
                  {exp.bullets.map((bullet: string, i: number) => (
                    <li key={i}>
                      <Marked value={bullet} highlights={highlights} />
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </Section>
      )}

      {hasItems(content.projects) && (
        <Section title="Projects">
          {content.projects.map((project: any, index: number) => (
            <div className="paper-entry" key={`project-${index}`}>
              <div className="paper-entry-head">
                <strong>
                  <Marked value={project.name} highlights={highlights} />
                </strong>
                {project.link && <em>{project.link}</em>}
              </div>
              {project.description && (
                <p className="paper-sub">
                  <Marked value={project.description} highlights={highlights} />
                </p>
              )}
              {hasItems(project.technologies) && (
                <p className="paper-sub">{project.technologies.join(" · ")}</p>
              )}
              {hasItems(project.bullets) && (
                <ul>
                  {project.bullets.map((bullet: string, i: number) => (
                    <li key={i}>
                      <Marked value={bullet} highlights={highlights} />
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </Section>
      )}

      {hasItems(content.skills) && (
        <Section title="Skills">
          {content.skills.map((group: any, index: number) => (
            <div className="skill-row" key={`skill-${index}`}>
              <b>{group.category || "Skills"}: </b>
              {(group.items || []).map((item: string, i: number) => (
                <span key={i}>
                  <Marked value={item} highlights={highlights} />
                  {i < group.items.length - 1 ? ", " : ""}
                </span>
              ))}
            </div>
          ))}
        </Section>
      )}

      {hasItems(content.education) && (
        <Section title="Education">
          {content.education.map((edu: any, index: number) => (
            <div className="paper-entry" key={`edu-${index}`}>
              <div className="paper-entry-head">
                <strong>
                  <Marked value={edu.institution} highlights={highlights} />
                </strong>
                <em>{dateRange(edu.start_date, edu.end_date)}</em>
              </div>
              <p className="paper-sub">
                {[edu.degree, edu.field_of_study].filter(Boolean).join(", ")}
                {edu.location ? ` — ${edu.location}` : ""}
              </p>
              {hasItems(edu.details) && (
                <ul>
                  {edu.details.map((detail: string, i: number) => (
                    <li key={i}>
                      <Marked value={detail} highlights={highlights} />
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </Section>
      )}

      {hasItems(content.certifications) && (
        <Section title="Certifications">
          {content.certifications.map((cert: any, index: number) => (
            <div className="skill-row" key={`cert-${index}`}>
              <b>
                <Marked value={cert.name} highlights={highlights} />
              </b>
              {cert.issuer ? ` — ${cert.issuer}` : ""}
              {cert.date ? ` (${cert.date})` : ""}
            </div>
          ))}
        </Section>
      )}

      {hasItems(content.languages) && (
        <Section title="Languages">
          {content.languages.map((lang: any, index: number) => (
            <div className="skill-row" key={`lang-${index}`}>
              <b>{lang.language}</b>
              {lang.proficiency ? ` — ${lang.proficiency}` : ""}
            </div>
          ))}
        </Section>
      )}

      {hasItems(content.custom_sections) &&
        content.custom_sections.map((section: any, index: number) => (
          <Section title={section.title || "Additional"} key={`custom-${index}`}>
            <ul>
              {(section.items || []).map((item: string, i: number) => (
                <li key={i}>
                  <Marked value={item} highlights={highlights} />
                </li>
              ))}
            </ul>
          </Section>
        ))}
    </article>
  );
}
