import type { SectionInfo } from "./conductorTypes";

export function SectionBanner({ section }: { section: SectionInfo }) {
  if (section === null) return null;
  const { index, total } = section as { index?: number; total?: number };
  const label =
    typeof index === "number" && typeof total === "number"
      ? `Section ${index}/${total}: ${section.name}`
      : `Section: ${section.name}`;
  return (
    <section key={section.name} aria-live="polite" className="min-w-0 animate-in fade-in duration-500 text-left">
      <p className="truncate text-sm font-semibold uppercase tracking-wide text-primary">{label}</p>
    </section>
  );
}
