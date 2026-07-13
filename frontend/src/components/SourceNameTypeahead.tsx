import { useEffect, useRef, useState } from "react";
import { suggestDataSources, type DataSourceSuggestion } from "../api";

const TYPEAHEAD_DEBOUNCE_MS = 300;
const MIN_QUERY_LEN = 2;

interface Props {
  value: string;
  placeholder?: string;
  onChange: (value: string) => void;
}

export default function SourceNameTypeahead({ value, placeholder, onChange }: Props) {
  const [suggestions, setSuggestions] = useState<DataSourceSuggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    const query = value.trim();
    if (query.length < MIN_QUERY_LEN) {
      setSuggestions([]);
      setOpen(false);
      setBusy(false);
      return;
    }

    setBusy(true);
    timer.current = setTimeout(() => {
      void suggestDataSources(query).then((matches) => {
        setSuggestions(matches);
        setOpen(matches.length > 0);
        setBusy(false);
      });
    }, TYPEAHEAD_DEBOUNCE_MS);

    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [value]);

  useEffect(() => {
    const onDocClick = (evt: MouseEvent) => {
      if (!rootRef.current?.contains(evt.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  const pick = (match: DataSourceSuggestion) => {
    onChange(match.source_name);
    setOpen(false);
  };

  return (
    <div className="typeahead" ref={rootRef}>
      <input
        type="text"
        value={value}
        placeholder={placeholder}
        autoComplete="off"
        onChange={(e) => onChange(e.target.value)}
        onFocus={() => {
          if (suggestions.length > 0) setOpen(true);
        }}
      />
      {busy && <span className="typeahead__status">Matching…</span>}
      {open && suggestions.length > 0 && (
        <ul className="typeahead__list" role="listbox">
          {suggestions.map((match) => (
            <li key={match.source_name}>
              <button
                type="button"
                className="typeahead__item"
                role="option"
                onClick={() => pick(match)}
              >
                <span className="typeahead__item-name">{match.source_name}</span>
                <span className="typeahead__item-meta">
                  {(match.match_confidence * 100).toFixed(0)}% match
                  {match.rationale ? ` · ${match.rationale}` : ""}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
