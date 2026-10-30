import { Command } from "@/components/Command";
import { LOCAL_RUN } from "@/lib/content";

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="skeleton" aria-hidden="true">
      {Array.from({ length: rows }, (_, i) => (
        <span key={i} className="skeleton__row" />
      ))}
    </div>
  );
}

export function Notice({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <section className="panel notice">
      <h2 className="notice__title">{title}</h2>
      {children}
    </section>
  );
}

export function LocalRun() {
  return (
    <section className="panel notice">
      <h2 className="notice__title">{LOCAL_RUN.title}</h2>
      <p className="muted">{LOCAL_RUN.body}</p>
      <ol className="cmds cmds--tight">
        {LOCAL_RUN.commands.map((c) => (
          <Command key={c.label} {...c} />
        ))}
      </ol>
      <p className="muted">{LOCAL_RUN.after}</p>
    </section>
  );
}
