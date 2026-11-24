import type { ReactNode } from "react";

function inline(text: string): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean);
  return parts.map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code key={index} style={{ padding: "1px 4px", borderRadius: 4, background: "#eef0f7", fontSize: "0.92em" }}>
          {part.slice(1, -1)}
        </code>
      );
    }
    return <span key={index}>{part}</span>;
  });
}

export default function SimpleMarkdown({ text }: { text: string }) {
  if (!text) return null;
  const lines = text.split("\n");
  const blocks: ReactNode[] = [];
  let list: string[] = [];

  function flushList() {
    if (list.length === 0) return;
    blocks.push(
      <ul key={`list-${blocks.length}`} style={{ margin: "6px 0", paddingLeft: 18 }}>
        {list.map((item, index) => (
          <li key={index} style={{ marginBottom: 2 }}>{inline(item)}</li>
        ))}
      </ul>
    );
    list = [];
  }

  for (const raw of lines) {
    const line = raw.trimEnd();
    if (/^\s*[-*•]\s+/.test(line)) {
      list.push(line.replace(/^\s*[-*•]\s+/, ""));
      continue;
    }
    flushList();
    if (!line.trim()) {
      blocks.push(<div key={`sp-${blocks.length}`} style={{ height: 6 }} />);
      continue;
    }
    if (line.startsWith("### ")) {
      blocks.push(
        <h4 key={blocks.length} style={{ margin: "10px 0 4px", fontSize: 13 }}>
          {inline(line.slice(4))}
        </h4>
      );
      continue;
    }
    if (line.startsWith("## ")) {
      blocks.push(
        <h3 key={blocks.length} style={{ margin: "10px 0 4px", fontSize: 14 }}>
          {inline(line.slice(3))}
        </h3>
      );
      continue;
    }
    blocks.push(
      <p key={blocks.length} style={{ margin: "3px 0" }}>
        {inline(line)}
      </p>
    );
  }
  flushList();
  return <div>{blocks}</div>;
}
