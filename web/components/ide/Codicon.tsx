import "@vscode/codicons/dist/codicon.css";

export function Codicon({ name, className = "" }: { name: string; className?: string }) {
  return <i className={`codicon codicon-${name} ${className}`} aria-hidden="true" />;
}
