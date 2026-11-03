// Build-time data loader: reads the community workflow examples under examples/
// and exposes their metadata to the docs site gallery.
import { existsSync, readFileSync } from "node:fs";
import { basename, dirname } from "node:path";

export interface ExampleMeta {
  id: string;
  title: string;
  description: string;
  category: string;
  features: string[];
  author: string;
  sourceUrl: string;
  dslVersion: string;
  event: string;
  repoPath: string; // e.g. examples/history-knowledge-qa
  hasPreview: boolean;
  previewPath: string;
}

declare const data: ExampleMeta[];
export { data };

function parseFrontmatter(md: string): Record<string, string | string[]> | null {
  const m = md.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  if (!m) return null;
  const out: Record<string, string | string[]> = {};
  let list: string | null = null;
  for (const raw of m[1].split(/\r?\n/)) {
    const line = raw.replace(/\s+$/, "");
    if (!line.trim()) continue;
    const item = line.match(/^\s+-\s+(.*)$/);
    if (item && list) {
      (out[list] as string[]).push(item[1].trim());
      continue;
    }
    const kv = line.match(/^([A-Za-z0-9_]+):\s*(.*)$/);
    if (kv) {
      const [, key, val] = kv;
      if (val === "") {
        out[key] = [];
        list = key;
      } else {
        out[key] = val.replace(/^["']|["']$/g, "");
        list = null;
      }
    }
  }
  return out;
}

export default {
  // Re-run when example metadata or preview availability changes.
  watch: ["../../examples/*/README.md", "../../examples/*/preview.png"],
  load(watchedFiles: string[]): ExampleMeta[] {
    const examples: ExampleMeta[] = [];
    for (const file of watchedFiles.filter((file) => basename(file) === "README.md")) {
      const id = basename(dirname(file));
      if (id === "TEMPLATE") continue;
      const fm = parseFrontmatter(readFileSync(file, "utf8"));
      if (!fm || !fm.id) continue;
      const previewPath = `${dirname(file)}/preview.png`;
      const hasPreview = existsSync(previewPath);
      examples.push({
        id: String(fm.id),
        title: String(fm.title ?? fm.id),
        description: String(fm.description ?? ""),
        category: String(fm.category ?? "other"),
        features: Array.isArray(fm.features) ? fm.features : [],
        author: String(fm.author ?? ""),
        sourceUrl: String(fm.sourceUrl ?? ""),
        dslVersion: String(fm.dslVersion ?? ""),
        event: String(fm.event ?? ""),
        repoPath: `examples/${id}`,
        hasPreview,
        previewPath: hasPreview ? `examples/${id}/preview.png` : ""
      });
    }
    return examples.sort((a, b) => a.category.localeCompare(b.category) || a.title.localeCompare(b.title));
  }
};
