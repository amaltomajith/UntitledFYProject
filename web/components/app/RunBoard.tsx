"use client";

import {
  ArrowCounterClockwise,
  BookOpen,
  Broom,
  Bug,
  Code,
  Eye,
  Flag,
  Flask,
  GitBranch,
  MagnifyingGlass,
  PencilLine,
  Power,
  TreeStructure,
  Warning,
  type Icon,
} from "@phosphor-icons/react";
import { AGENTS, pad2, type IconName, type Tier } from "@/lib/agents";
import { RUN_VIEW } from "@/lib/content";
import { runTokens, shortName, testsNow, type RunStep, type RunView } from "@/lib/runEvents";
import { Columns } from "./Charts";

const ICONS: Record<IconName, Icon> = {
  MagnifyingGlass,
  TreeStructure,
  Code,
  Flask,
  Bug,
  Broom,
  Eye,
  PencilLine,
  BookOpen,
  GitBranch,
  Flag,
  Power,
};

const TIERS: Tier[] = ["zero-token", "fast", "quality", "injected"];

const fmt = (n: number) => n.toLocaleString("en-US");

function AgentRail({ view }: { view: RunView }) {
  const used = new Map<string, number>();
  for (const s of view.steps) {
    const name = shortName(s.agent);
    used.set(name, (used.get(name) ?? 0) + 1);
  }
  return (
    <section className="panel rail" aria-label={RUN_VIEW.agents}>
      {TIERS.map((tier) => (
        <div key={tier} className="rail__tier">
          <h3 className="label">{RUN_VIEW.tiers[tier]}</h3>
          <ul>
            {AGENTS.filter((a) => a.tier === tier).map((a) => {
              const Glyph = ICONS[a.icon];
              const count = used.get(a.name) ?? 0;
              const acting = view.acting === a.name;
              return (
                <li key={a.name} className="rail__agent" data-used={count > 0 || undefined} data-acting={acting || undefined}>
                  <Glyph size={16} aria-hidden="true" />
                  <span>{a.name}</span>
                  {acting ? <span className="sr-only">{RUN_VIEW.acting}</span> : null}
                  {count > 0 ? <span className="rail__count">{count}</span> : null}
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </section>
  );
}

function StepItem({ step }: { step: RunStep }) {
  const action = step.verdict?.action;
  const rolled = action === "rollback" || Boolean(step.rollback_reason);
  return (
    <li className="tstep" data-rolled={rolled || undefined}>
      <span className="tstep__n">{pad2(step.step + 1)}</span>
      <div className="tstep__body">
        <div className="tstep__head">
          <strong>{shortName(step.agent)}</strong>
          {action ? <span className="tag">{RUN_VIEW.verdicts[action] ?? action}</span> : null}
          <span className="tstep__meta">
            {step.tokens ? `${fmt(step.tokens)} tokens` : RUN_VIEW.zero}
            {step.prompt_chars ? ` · ${fmt(step.prompt_chars)} chars` : ""}
            {` · ${step.seconds.toFixed(1)}s`}
          </span>
        </div>
        {step.model && step.model !== "none" ? <p className="tstep__model">{step.model}</p> : null}
        {step.rollback_reason ? (
          <p className="tstep__note">
            <ArrowCounterClockwise size={14} aria-hidden="true" />
            {RUN_VIEW.rolledBack}: {step.rollback_reason}
          </p>
        ) : null}
        {step.error ? (
          <p className="tstep__note tstep__note--bad">
            <Warning size={14} aria-hidden="true" />
            {step.error}
          </p>
        ) : null}
        {step.content.trim() ? (
          <details className="tstep__output">
            <summary>{RUN_VIEW.output}</summary>
            <pre>{step.content}</pre>
          </details>
        ) : null}
      </div>
    </li>
  );
}

function Timeline({ view }: { view: RunView }) {
  return (
    <section className="panel timeline" aria-label={RUN_VIEW.timeline}>
      <h3 className="label">{RUN_VIEW.timeline}</h3>
      {view.steps.length === 0 && !view.acting ? <p className="muted">{RUN_VIEW.waiting}</p> : null}
      <ol role="log" aria-live="polite">
        {view.steps.map((s) => (
          <StepItem key={s.step} step={s} />
        ))}
      </ol>
      {view.acting ? (
        <p className="timeline__acting">
          <span className="pulse" aria-hidden="true" />
          {view.acting} {RUN_VIEW.acting}
        </p>
      ) : null}
    </section>
  );
}

function Side({ view }: { view: RunView }) {
  const tests = testsNow(view);
  const prompts = view.steps
    .filter((s) => s.prompt_chars > 0)
    .map((s) => ({
      x: s.step + 1,
      y: s.prompt_chars,
      label: `Step ${s.step + 1}, ${shortName(s.agent)}: ${fmt(s.prompt_chars)} chars`,
    }));
  return (
    <aside className="side">
      <div className="panel stat">
        <h3 className="label">{RUN_VIEW.tests}</h3>
        <p className="stat__value">
          {tests ? tests.passed : "0"}
          <span className="stat__of"> / {tests ? tests.total : "0"}</span>
        </p>
        <span className="meter" aria-hidden="true">
          <span style={{ width: `${tests && tests.total ? (tests.passed / tests.total) * 100 : 0}%` }} />
        </span>
      </div>
      <div className="panel stat">
        <h3 className="label">{RUN_VIEW.tokens}</h3>
        <p className="stat__value">{fmt(runTokens(view.steps))}</p>
        <p className="muted muted--sm">{RUN_VIEW.tokensNote}</p>
      </div>
      <div className="panel stat">
        <h3 className="label">{RUN_VIEW.prompt}</h3>
        {prompts.length ? (
          <Columns points={prompts} format={fmt} xName="Step" compact />
        ) : (
          <p className="muted">{RUN_VIEW.promptEmpty}</p>
        )}
        <p className="muted muted--sm">{RUN_VIEW.promptNote}</p>
      </div>
    </aside>
  );
}

export function RunBoard({ view }: { view: RunView }) {
  return (
    <div className="board">
      <AgentRail view={view} />
      <Timeline view={view} />
      <Side view={view} />
    </div>
  );
}
