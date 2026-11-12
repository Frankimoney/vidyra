import { useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import type { SavedCharacter } from "../services/api";

type Props = {
  value: string;
  onChange: (value: string) => void;
  characters: SavedCharacter[];
  onMention?: (character: SavedCharacter) => void;
  placeholder?: string;
  rows?: number;
  maxLength?: number;
  className?: string;
  onKeyDown?: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
};

const MENU_ITEMS = 6;

export default function CharacterMentions({
  value,
  onChange,
  characters,
  onMention,
  placeholder,
  rows = 3,
  maxLength,
  className = "textarea",
  onKeyDown,
}: Props) {
  const ref = useRef<HTMLTextAreaElement | null>(null);
  const [mentionStart, setMentionStart] = useState<number | null>(null);
  const [query, setQuery] = useState("");

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const list = needle
      ? characters.filter(
          (c) =>
            c.name.toLowerCase().includes(needle) ||
            c.description.toLowerCase().includes(needle)
        )
      : characters;
    return list.slice(0, MENU_ITEMS);
  }, [characters, query]);

  function syncMention(nextValue: string, caret: number) {
    const before = nextValue.slice(0, caret);
    const at = before.lastIndexOf("@");
    if (at < 0) {
      setMentionStart(null);
      setQuery("");
      return;
    }
    const previous = at === 0 ? "" : before[at - 1];
    if (previous && !/\s/.test(previous)) {
      setMentionStart(null);
      setQuery("");
      return;
    }
    setMentionStart(at);
    setQuery(before.slice(at + 1));
  }

  function change(nextValue: string) {
    onChange(nextValue);
    const caret = ref.current?.selectionStart ?? nextValue.length;
    syncMention(nextValue, caret);
  }

  function choose(character: SavedCharacter) {
    const caret = ref.current?.selectionStart ?? value.length;
    const start = mentionStart ?? value.lastIndexOf("@");
    if (start < 0) return;
    const next = `${value.slice(0, start)}@${character.name} ${value.slice(caret)}`;
    onChange(next);
    onMention?.(character);
    setMentionStart(null);
    setQuery("");
    requestAnimationFrame(() => {
      const node = ref.current;
      if (!node) return;
      const pos = start + character.name.length + 2;
      node.focus();
      node.setSelectionRange(pos, pos);
    });
  }

  const open = mentionStart !== null && characters.length > 0;

  return (
    <div style={{ position: "relative" }}>
      <textarea
        ref={ref}
        className={className}
        rows={rows}
        maxLength={maxLength}
        placeholder={placeholder}
        value={value}
        onChange={(e) => change(e.target.value)}
        onKeyDown={(e) => {
          if (open && (e.key === "Enter" || e.key === "Tab") && matches.length > 0) {
            e.preventDefault();
            choose(matches[0]);
            return;
          }
          if (open && e.key === "Escape") {
            setMentionStart(null);
            setQuery("");
            e.stopPropagation();
            return;
          }
          onKeyDown?.(e);
        }}
        onClick={() => {
          const node = ref.current;
          if (node) syncMention(node.value, node.selectionStart ?? 0);
        }}
        onKeyUp={() => {
          const node = ref.current;
          if (node) syncMention(node.value, node.selectionStart ?? 0);
        }}
        onBlur={() => {
          window.setTimeout(() => {
            setMentionStart(null);
            setQuery("");
          }, 150);
        }}
      />
      {open && (
        <div
          role="listbox"
          aria-label="Characters"
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            left: 0,
            right: 0,
            zIndex: 20,
            display: "grid",
            gap: 2,
            padding: 4,
            borderRadius: 10,
            border: "1px solid var(--border)",
            background: "var(--surface)",
            boxShadow: "0 12px 30px rgba(0,0,0,0.35)",
            maxHeight: 240,
            overflowY: "auto",
          }}
        >
          {matches.length === 0 ? (
            <div className="faint" style={{ padding: "8px 10px", fontSize: "0.8rem" }}>
              No saved character matches “{query}”.
            </div>
          ) : (
            matches.map((character) => (
              <button
                key={character.character_id}
                type="button"
                role="option"
                aria-selected="false"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => choose(character)}
                style={{
                  display: "grid",
                  gap: 1,
                  textAlign: "left",
                  padding: "7px 9px",
                  border: "none",
                  borderRadius: 7,
                  background: "transparent",
                  color: "var(--text)",
                  cursor: "pointer",
                  font: "inherit",
                }}
              >
                <span style={{ fontWeight: 700, fontSize: "0.84rem" }}>@{character.name}</span>
                {character.description && (
                  <span className="faint" style={{ fontSize: "0.74rem" }}>
                    {character.description.slice(0, 90)}
                  </span>
                )}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
