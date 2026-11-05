import map from "@/lib/fileIcons.json";

const extensions: Record<string, string> = map.extensions;
const names: Record<string, string> = map.names;
const folders: Record<string, string> = map.folders;

function iconFor(name: string) {
  const lower = name.toLowerCase();
  if (names[lower]) return names[lower];
  const parts = lower.split(".");
  for (let i = 1; i < parts.length; i++) {
    const icon = extensions[parts.slice(i).join(".")];     // `a.test.ts` tries `test.ts` before `ts`
    if (icon) return icon;
  }
  return map.file;
}

const Logo = ({ icon }: { icon: string }) => (
  <img className="ide-logo" src={`/file-icons/${icon}.svg`} width={16} height={16} alt="" aria-hidden="true" draggable={false} />
);

export const FileIcon = ({ name }: { name: string }) => <Logo icon={iconFor(name)} />;

export function FolderIcon({ name, open }: { name: string; open: boolean }) {
  const icon = folders[name.toLowerCase()] ?? map.folder;
  return <Logo icon={open ? `${icon}-open` : icon} />;
}
