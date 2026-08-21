const SUMMARY_SEPARATORS = ":：；;,，。.!?！？";

function hasBalancedNonEmptyEmphasis(statement: string): boolean {
  const matches = statement.match(/\*\*([^*\n]+?)\*\*/g) ?? [];
  return matches.length > 0 && (statement.match(/\*\*/g) ?? []).length === matches.length * 2;
}

function visibleLength(text: string): number {
  return [...text].filter((char) => !/\s/.test(char)).length;
}

export function emphasizeSummaryStatement(statement: string): string {
  if (!statement.trim()) return statement;
  if (hasBalancedNonEmptyEmphasis(statement)) return statement;

  const cleaned = statement.replace(/\*\*/g, "");
  if (!cleaned.trim()) return cleaned;

  for (let index = 0; index < cleaned.length; index += 1) {
    if (!SUMMARY_SEPARATORS.includes(cleaned[index] ?? "")) continue;
    const prefix = cleaned.slice(0, index);
    if (visibleLength(prefix) >= 6) {
      return `**${prefix}**${cleaned.slice(index)}`;
    }
  }

  return `**${cleaned}**`;
}

function isSourceLine(line: string): boolean {
  return /^\s*\*来源：.*\*\s*$/.test(line);
}

export function emphasizeCoreSummaryMarkdown(markdown: string): string {
  const newline = markdown.includes("\r\n") ? "\r\n" : "\n";
  const lines = markdown.split(newline);
  let inCoreSummary = false;

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index] ?? "";
    if (/^##\s+核心结论\s*$/.test(line)) {
      inCoreSummary = true;
      continue;
    }
    if (/^##\s+/.test(line)) {
      inCoreSummary = false;
      continue;
    }
    if (inCoreSummary && /^#{3,6}\s+Sources\s*$/i.test(line)) {
      inCoreSummary = false;
      continue;
    }
    if (!inCoreSummary || !line.trim() || isSourceLine(line)) continue;

    const match = line.match(/^(\s*(?:[-*+]\s+)?)(.*?)(\s*)$/);
    if (!match) continue;
    const [, prefix, content, suffix] = match;
    if (content) lines[index] = `${prefix}${emphasizeSummaryStatement(content)}${suffix}`;
  }

  return lines.join(newline);
}
