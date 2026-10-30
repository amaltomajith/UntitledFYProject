"use client";

import { useMemo, useState } from "react";
import { CaretDown, CaretRight, File } from "@phosphor-icons/react";
import { IDE } from "@/lib/content";

type Node = { name: string; path: string; children: Node[] | null };

function build(files: string[]): Node[] {
  const root: Node = { name: "", path: "", children: [] };
  for (const file of files) {
    let at = root;
    const parts = file.split("/");
    parts.forEach((name, i) => {
      const path = parts.slice(0, i + 1).join("/");
      const isFile = i === parts.length - 1;
      let child = at.children!.find((c) => c.name === name);
      if (!child) {
        child = { name, path, children: isFile ? null : [] };
        at.children!.push(child);
      }
      at = child;
    });
  }
  const sort = (nodes: Node[]) => {
    nodes.sort((a, b) => Number(a.children === null) - Number(b.children === null) || a.name.localeCompare(b.name));
    nodes.forEach((n) => n.children && sort(n.children));
    return nodes;
  };
  return sort(root.children!);
}

type Props = {
  files: string[];
  changed: Record<string, string>;
  active: string | null;
  onOpen: (path: string) => void;
};

function Row({ node, depth, ...props }: Props & { node: Node; depth: number }) {
  const [open, setOpen] = useState(depth < 1);
  const pad = { paddingLeft: 10 + depth * 14 };

  if (node.children) {
    return (
      <li>
        <button type="button" className="ide-tree__row" style={pad} aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? <CaretDown size={12} aria-hidden="true" /> : <CaretRight size={12} aria-hidden="true" />}
          <span>{node.name}</span>
        </button>
        {open ? (
          <ul>
            {node.children.map((child) => (
              <Row key={child.path} node={child} depth={depth + 1} {...props} />
            ))}
          </ul>
        ) : null}
      </li>
    );
  }

  const change = props.changed[node.path];
  return (
    <li>
      <button
        type="button"
        className="ide-tree__row"
        style={pad}
        aria-current={props.active === node.path ? "true" : undefined}
        onClick={() => props.onOpen(node.path)}
      >
        <File size={13} aria-hidden="true" />
        <span>{node.name}</span>
        {change ? <span className="ide-tree__mark">{IDE.tree.marks[change] ?? change}</span> : null}
      </button>
    </li>
  );
}

export function FileTree(props: Props) {
  const nodes = useMemo(() => build(props.files), [props.files]);
  return (
    <nav className="ide-tree" aria-label={IDE.tree.label}>
      {nodes.length ? (
        <ul>
          {nodes.map((node) => (
            <Row key={node.path} node={node} depth={0} {...props} />
          ))}
        </ul>
      ) : (
        <p className="ide-tree__empty">{IDE.tree.empty}</p>
      )}
    </nav>
  );
}
